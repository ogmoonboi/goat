from abc import abstractmethod
from typing import Dict, Optional, TypedDict, List, Any

from solana.rpc.api import Client as SolanaClient
from solana.rpc.types import TxOpts
from solana.rpc.commitment import Confirmed
from solana.transaction import Transaction
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.instruction import Instruction, AccountMeta, CompiledInstruction
from solders.message import Message, MessageV0
from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.transaction import VersionedTransaction
import nacl.signing

from goat.classes.wallet_client_base import Balance, Signature, WalletClientBase
from goat.types.chain import Chain


class SolanaTransaction(TypedDict):
    """Transaction parameters for Solana transactions."""

    instructions: List[Instruction]
    address_lookup_table_addresses: Optional[List[str]]
    accounts_to_sign: Optional[List[Keypair]]


class SolanaOptions:
    """Configuration options for Solana wallet clients."""

    def __init__(self):
        pass


class SolanaWalletClient(WalletClientBase):
    """Base class for Solana wallet implementations."""

    def __init__(self, client: SolanaClient):
        """Initialize the Solana wallet client.

        Args:
            client: A Solana RPC client instance
        """
        super().__init__()
        self.client = client

    def get_chain(self) -> Chain:
        """Get the chain type for Solana."""
        return {"type": "solana"}

    @abstractmethod
    def get_address(self) -> str:
        """Get the wallet's public address."""
        pass

    @abstractmethod
    def sign_message(self, message: str) -> Signature:
        """Sign a message with the wallet's private key."""
        pass

    @abstractmethod
    def balance_of(self, address: str) -> Balance:
        """Get the SOL balance of an address."""
        pass

    @abstractmethod
    def send_transaction(self, transaction: SolanaTransaction) -> Dict[str, str]:
        """Send a transaction on the Solana chain.

        Args:
            transaction: Transaction parameters including instructions and optional lookup tables

        Returns:
            Dict containing the transaction hash
        """
        pass

    def decompile_versioned_transaction_to_instructions(self, versioned_transaction: VersionedTransaction) -> List[Instruction]:
        """Decompile a versioned transaction into its constituent instructions.

        Args:
            versioned_transaction: The versioned transaction to decompile

        Returns:
            List of instructions from the transaction
        """
        # Convert CompiledInstructions back to Instructions
        instructions = []
        for compiled_ix in versioned_transaction.message.instructions:
            # Get the program ID from the account keys
            program_id = versioned_transaction.message.account_keys[compiled_ix.program_id_index]
            
            # Get the account metas
            accounts = []
            for account_idx in compiled_ix.accounts:
                pubkey = versioned_transaction.message.account_keys[account_idx]
                is_signer = versioned_transaction.message.is_signer(account_idx)
                is_writable = versioned_transaction.message.is_writable(account_idx)
                accounts.append(AccountMeta(pubkey, is_signer, is_writable))

            # Create the instruction
            ix = Instruction(program_id, compiled_ix.data, accounts)
            instructions.append(ix)

        return instructions

    def get_address_lookup_table_accounts(self, keys: List[str]) -> List[AddressLookupTableAccount]:
        """Get address lookup table accounts for the given addresses.

        Args:
            addresses: List of lookup table addresses

        Returns:
            List of address lookup table accounts
        """
        # Convert addresses to Pubkeys
        pubkeys = [Pubkey.from_string(addr) for addr in keys]

        # Get account info for each lookup table
        account_infos = self.client.get_multiple_accounts(pubkeys).value

        # Create lookup table accounts for non-null accounts
        lookup_table_accounts = []
        for i, account_info in enumerate(account_infos):
            if account_info is not None:
                lookup_table_account = AddressLookupTableAccount.from_bytes(
                    account_info.data
                )
                lookup_table_accounts.append(lookup_table_account)

        return lookup_table_accounts


class SolanaKeypairWalletClient(SolanaWalletClient):
    """Solana wallet implementation using a keypair."""

    def __init__(self, client: SolanaClient, keypair: Keypair):
        """Initialize the Solana keypair wallet client.

        Args:
            client: A Solana RPC client instance
            keypair: A Solana keypair for signing transactions
        """
        super().__init__(client)
        self.keypair = keypair

    def get_address(self) -> str:
        """Get the wallet's public address."""
        return str(self.keypair.pubkey())

    def sign_message(self, message: str) -> Signature:
        """Sign a message with the wallet's private key."""
        message_bytes = message.encode("utf-8")
        signed = nacl.signing.SigningKey(self.keypair.secret()).sign(message_bytes)
        return {"signature": signed.signature.hex()}

    def balance_of(self, address: str) -> Balance:
        """Get the SOL balance of an address."""
        pubkey = Pubkey.from_string(address)
        balance_lamports = self.client.get_balance(pubkey).value
        # Convert lamports (1e9 lamports in 1 SOL)
        return {
            "decimals": 9,
            "symbol": "SOL",
            "name": "Solana",
            "value": str(balance_lamports / 10**9),
            "in_base_units": str(balance_lamports),
        }

    def send_transaction(self, transaction: SolanaTransaction) -> Dict[str, str]:
        """Send a transaction on the Solana chain."""
        # Get latest blockhash
        recent_blockhash = self.client.get_latest_blockhash().value.blockhash

        # Create transaction
        tx = Transaction()
        tx.recent_blockhash = recent_blockhash
        tx.fee_payer = self.keypair.pubkey()

        # Add instructions
        for instruction in transaction["instructions"]:
            tx.add(instruction)

        # Add signers
        signers = [self.keypair]
        additional_signers = transaction.get("accounts_to_sign")
        if additional_signers is not None:
            signers.extend(additional_signers)

        # Sign and send transaction
        tx.sign(*signers)
        result = self.client.send_transaction(
            tx,
            *signers,
            opts=TxOpts(
                skip_preflight=False,
                max_retries=10,
                preflight_commitment=Confirmed,
            ),
        )

        # Wait for confirmation
        self.client.confirm_transaction(
            result.value,
            commitment=Confirmed,
        )

        return {"hash": str(result.value)}


def solana(client: SolanaClient, keypair: Keypair) -> SolanaKeypairWalletClient:
    """Create a new SolanaKeypairWalletClient instance.

    Args:
        client: A Solana RPC client instance
        keypair: A Solana keypair for signing transactions

    Returns:
        A new SolanaKeypairWalletClient instance
    """
    return SolanaKeypairWalletClient(client, keypair)
