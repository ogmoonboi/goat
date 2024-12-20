import { openai } from "@ai-sdk/openai";
import { generateText } from "ai";

import { getOnChainTools } from "@goat-sdk/adapter-vercel-ai";
import { sendSOL, solana } from "@goat-sdk/wallet-solana";

import { Connection, Keypair } from "@solana/web3.js";

import { jupiter } from "@goat-sdk/plugin-jupiter";
import { splToken } from "@goat-sdk/plugin-spl-token";
import { coingecko } from "@goat-sdk/plugin-coingecko";
import base58 from "bs58";

import readline from 'readline';

require("dotenv").config();

const connection = new Connection(process.env.SOLANA_RPC_URL as string);
const keypair = Keypair.fromSecretKey(base58.decode(process.env.SOLANA_PRIVATE_KEY as string));

const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout
});

type Message = {
    role: 'user' | 'assistant';
    content: string;
};

async function chat() {
    const conversationHistory: Message[] = [];
    const tools = await getOnChainTools({
        wallet: solana({
            keypair,
            connection,
        }),
        plugins: [sendSOL(), jupiter(), splToken(), coingecko({
            apiKey: process.env.COINGECKO_API_KEY as string
        })],
    });

    console.log("Chat started. Type 'exit' to end the conversation.");
    
    const askQuestion = () => {
        rl.question('You: ', async (prompt) => {
            if (prompt.toLowerCase() === 'exit') {
                rl.close();
                return;
            }

            conversationHistory.push({ role: 'user', content: prompt });
            
            const result = await generateText({
                model: openai("gpt-4o-mini"),
                tools: tools,
                maxSteps: 10,
                prompt: `You are a based crypto degen assistant. You're knowledgeable about DeFi, NFTs, and trading. You use crypto slang naturally and stay up to date with Solana ecosystem. You help users with their trades and provide market insights. Keep responses concise and use emojis occasionally.

Previous conversation:
${conversationHistory
    .map(m => `${m.role}: ${m.content}`)
    .join('\n')}

Current request: ${prompt}`,
            });

            conversationHistory.push({ role: 'assistant', content: result.text });
            console.log('Assistant:', result.text);
            askQuestion();
        });
    };

    askQuestion();
}

chat().catch(console.error);
