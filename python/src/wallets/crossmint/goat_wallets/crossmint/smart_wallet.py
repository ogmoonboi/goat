import asyncio
from typing import Dict, List, Optional, TypedDict, Union, TypeVar, cast
from goat_wallets.evm import EVMWalletClient, EVMTransaction, EVMReadRequest, EVMTypedData
from eth_typing import HexStr
from web3.main import AsyncWeb3
from web3.types import Wei
from web3.providers.async_rpc import AsyncHTTPProvider
from web3.eth import AsyncEth
from web3._utils.validation import validate_address
from web3._utils.encoding import to_hex, to_bytes
from eth_account.messages import encode_defunct
from eth_account import Account
from .api_client import CrossmintWalletsAPI, Call

CustodialSigner = str
KeyPairSigner = TypedDict('KeyPairSigner', {
    'secretKey': str,
    'address': str
})
Signer = Union[CustodialSigner, KeyPairSigner]

# Use sync Web3 for encoding and address utilities
w3_sync = Web3()


def get_locator(address: Optional[str] = None, linked_user: Optional[Dict] = None) -> str:
    """Get wallet locator from address or linked user."""
    if linked_user:
        if "email" in linked_user:
            return f"email:{linked_user['email']}:evm-smart-wallet"
        if "phone" in linked_user:
            return f"phone:{linked_user['phone']}:evm-smart-wallet"
        if "userId" in linked_user:
            return f"userId:{linked_user['userId']}:evm-smart-wallet"
    
    if not address:
        raise ValueError("A Smart Wallet address is required if no linked user is provided")
    
    return address


def build_transaction_data(
    recipient_address: str,
    abi: Optional[List] = None,
    function_name: Optional[str] = None,
    args: Optional[List] = None,
    value: Optional[int] = None
) -> Call:
    """Build transaction data for smart wallet calls."""
    if not abi:
        return Call(
            to=recipient_address,
            value=str(value or 0),
            data="0x"
        )
    
    if not function_name:
        raise ValueError("Function name is required when ABI is provided")
    
    # Create Web3 contract object for encoding function data
    contract = w3_sync.eth.contract(
        address=w3_sync.to_checksum_address(recipient_address),
        abi=abi
    )
    data = contract.encodeFunction(function_name, args or [])
    
    return Call(
        to=recipient_address,
        value=str(value or 0),
        data=data.hex()
    )


class SmartWalletClient(EVMWalletClient):
    """EVM Smart Wallet implementation using Crossmint."""
    
    def __init__(
        self,
        address: str,
        api_client: CrossmintWalletsAPI,
        chain: str,
        signer: Signer,
        provider_url: str,
        ens_provider_url: Optional[str] = None
    ):
        """Initialize Smart Wallet client.
        
        Args:
            address: Wallet address
            api_client: Crossmint API client
            chain: Chain identifier
            signer: Signer configuration (address string or keypair dict)
            provider_url: RPC provider URL
            ens_provider_url: Optional ENS provider URL
        """
        super().__init__()
        self._address = address
        self._client = api_client
        self._chain = chain
        self._signer = signer
        
        # Initialize Web3 providers
        self._w3 = AsyncWeb3(AsyncHTTPProvider(provider_url))
        self._ens = AsyncWeb3(AsyncHTTPProvider(ens_provider_url)) if ens_provider_url else None
        
        # Get locator
        self._locator = get_locator(address)
    
    @property
    def has_custodial_signer(self) -> bool:
        """Check if using custodial signer."""
        return isinstance(self._signer, str)
    
    @property
    def secret_key(self) -> Optional[str]:
        """Get secret key if using keypair signer."""
        return cast(KeyPairSigner, self._signer)["secretKey"] if not self.has_custodial_signer else None
        
    @property
    def signerAccount(self) -> Optional[Account]:
        """Get signer account if using keypair signer."""
        if self.has_custodial_signer:
            return None
        return Account.from_key(cast(KeyPairSigner, self._signer)["secretKey"])
    
    def get_address(self) -> str:
        """Get wallet address."""
        return self._address
    
    def get_chain(self) -> Dict:
        """Get chain information."""
        return {
            "type": "evm",
            "id": self._w3.eth.chain_id
        }
    
    async def resolve_address(self, address: str) -> str:
        """Resolve ENS name to address."""
        try:
            validate_address(address)
            return w3_sync.to_checksum_address(address)
        except ValueError:
            if not self._ens:
                raise ValueError("ENS provider is not configured")
            
            try:
                resolved = await self._ens.ens.resolve_name(address)
                if not resolved:
                    raise ValueError("ENS name could not be resolved")
                return w3_sync.to_checksum_address(resolved)
            except Exception as e:
                raise ValueError(f"Failed to resolve ENS name: {e}")
    
    async def sign_message(self, message: str) -> Dict[str, str]:
        """Sign a message with the wallet's private key.
        
        Args:
            message: Message to sign
            
        Returns:
            Dict containing the signature
            
        Raises:
            ValueError: If signature fails or is undefined
        """
        # Get signature ID and approvals
        signer_address = None
        if not self.has_custodial_signer:
            account = self.signerAccount
            if not account:
                raise ValueError("Signer account is not available")
            signer_address = account.address
            
        response = await self._client.sign_message_for_smart_wallet(
            self._address,
            message,
            self._chain,
            signer_address
        )
        signature_id = response["id"]
        approvals = response.get("approvals", {})
        
        # Handle non-custodial signing
        if not self.has_custodial_signer:
            account = self.signerAccount
            if not account:
                raise ValueError("Signer account is not available")
            
            # Get message to sign from pending approvals
            pending_approvals = approvals.get("pending", [])
            if not pending_approvals:
                raise ValueError("No pending approvals found")
                
            to_sign = pending_approvals[0].get("message")
            if not to_sign:
                raise ValueError("No message to sign in approvals")
            
            # Sign with account
            signature = account.sign_message(
                encode_defunct(hexstr=to_sign)
            ).signature.hex()
            
            # Submit approval
            await self._client.approve_signature_for_smart_wallet(
                signature_id,
                self._address,
                f"evm-keypair:{account.address}",
                signature
            )
        
        # Poll for signature status
        while True:
            status = await self._client.check_signature_status(
                signature_id,
                self._address
            )
            
            if status["status"] == "success":
                if not status.get("outputSignature"):
                    raise ValueError("Signature is undefined")
                return {"signature": status["outputSignature"]}
            
            if status["status"] == "failed":
                raise ValueError("Signature failed")
            
            await asyncio.sleep(2)  # Wait 2 seconds before checking again
    
    async def sign_typed_data(self, data: EVMTypedData) -> Dict[str, str]:
        """Sign typed data."""
        if not isinstance(self._signer, dict):
            raise ValueError("Keypair signer is required for typed data signing")
        
        response = await self._client.sign_typed_data_for_smart_wallet(
            self._address,
            data,
            self._chain,
            cast(KeyPairSigner, self._signer)["address"]
        )
        
        if not self.has_custodial_signer:
            if not self.secret_key:
                raise ValueError("Signer account is not available")
            
            to_sign = response["approvals"]["pending"][0]["message"]
            account = self._w3.eth.account.from_key(self.secret_key)
            signature = account.sign_message(
                encode_defunct(hexstr=to_sign)
            ).signature.hex()
            
            await self._client.approve_signature_for_smart_wallet(
                response["id"],
                self._address,
                f"evm-keypair:{account.address}",
                signature
            )
        
        while True:
            status = await self._client.check_signature_status(
                response["id"],
                self._address
            )
            
            if status["status"] == "success":
                if not status.get("outputSignature"):
                    raise ValueError("Signature is undefined")
                return {"signature": status["outputSignature"]}
            
            if status["status"] == "failed":
                raise ValueError("Signature failed")
            
            await asyncio.sleep(2)
    
    async def send_transaction(self, transaction: EVMTransaction) -> Dict[str, str]:
        """Send a single transaction."""
        return await self._send_batch_of_transactions([transaction])
    
    async def send_batch_of_transactions(
        self, transactions: List[EVMTransaction]
    ) -> Dict[str, str]:
        """Send multiple transactions as a batch."""
        return await self._send_batch_of_transactions(transactions)
    
    async def read(self, request: EVMReadRequest) -> Dict:
        """Read data from a smart contract.
        
        Args:
            request: Read request parameters including address, ABI, function name and args
            
        Returns:
            Dict containing the result value
            
        Raises:
            ValueError: If ABI is not provided
        """
        # Extract request parameters
        address = request.get("address")
        abi = request.get("abi")
        function_name = request.get("functionName")
        args = request.get("args", [])
        
        if not abi:
            raise ValueError("Read request must include ABI for EVM")
        
        # Create contract instance and call function
        result = await self._w3.eth.read_contract({
            "address": await self.resolve_address(address),
            "abi": abi,
            "functionName": function_name,
            "args": args
        })
        
        return {"value": result}
    
    async def balance_of(self, address: str) -> Dict:
        """Get ETH balance of an address."""
        resolved = await self.resolve_address(address)
        balance = await self._w3.eth.get_balance(w3_sync.to_checksum_address(resolved))
        
        return {
            "decimals": 18,
            "symbol": "ETH",
            "name": "Ethereum",
            "value": str(w3_sync.from_wei(balance, "ether")),
            "inBaseUnits": str(balance)
        }
    
    async def _send_batch_of_transactions(
        self, transactions: List[EVMTransaction]
    ) -> Dict[str, str]:
        """Internal method to send batch transactions."""
        transaction_data = [
            build_transaction_data(
                tx["to"],
                tx.get("abi"),
                tx.get("functionName"),
                tx.get("args"),
                tx.get("value", 0)
            )
            for tx in transactions
        ]
        
        response = await self._client.create_transaction_for_smart_wallet(
            self._address,
            transaction_data,
            self._chain,
            None if self.has_custodial_signer else cast(KeyPairSigner, self._signer)["address"]
        )
        
        if not self.has_custodial_signer:
            if not self.secret_key:
                raise ValueError("Signer account is not available")
            
            user_op_hash = response["approvals"]["pending"][0]["message"]
            if not user_op_hash:
                raise ValueError("User operation hash is not available")
            
            account = self._w3.eth.account.from_key(self.secret_key)
            signature = account.sign_message(
                encode_defunct(hexstr=user_op_hash)
            ).signature.hex()
            
            await self._client.approve_transaction(
                self._locator,
                response["id"],
                [{
                    "signature": signature,
                    "signer": f"evm-keypair:{account.address}"
                }]
            )
        
        while True:
            status = await self._client.check_transaction_status(
                self._locator,
                response["id"]
            )
            
            if status["status"] in ["success", "failed"]:
                return {
                    "hash": status.get("onChain", {}).get("txId", ""),
                    "status": status["status"]
                }
            
            await asyncio.sleep(2)


def smart_wallet_factory(api_client: CrossmintWalletsAPI):
    """Factory function to create smart wallet instances."""
    async def create_smart_wallet(options: Dict) -> SmartWalletClient:
        """Create a new smart wallet instance."""
        locator = get_locator(options.get("address"), options.get("linkedUser"))
        wallet = await api_client.get_wallet(locator)
        
        return SmartWalletClient(
            wallet["address"],
            api_client,
            options["chain"],
            options["signer"],
            options["provider"],
            options.get("options", {}).get("ensProvider")
        )
    
    return create_smart_wallet
