import asyncio
import sys
import os
from utils.llm_service import ask_llm

class TerminalClient:
    """Terminal interface for the LLM bot"""
    
    def __init__(self, user_id="terminal_user"):
        self.user_id = user_id
        self.channel_id = "terminal"
        self.username = "User"
        self.running = False
    
    async def chat_loop(self):
        """Main chat loop for terminal"""
        print("🤖 Terminal LLM Chat")
        print("Type 'quit' or 'exit' to exit")
        print("-" * 40)
        
        conversation_history = []
        
        while self.running:
            try:
                # Get user input
                user_input = input("\nYou: ").strip()
                
                if user_input.lower() in ['quit', 'exit', 'q']:
                    print("Goodbye!")
                    break
                
                if not user_input:
                    continue
                
                # Get response from LLM
                print("🤖 Thinking...", end="\r")
                
                response = await ask_llm(
                    self.user_id,
                    self.channel_id,
                    user_input,
                    self.username,
                    is_continuation=False,
                    platform="terminal"
                )
                
                # Clear the "Thinking..." line
                print(" " * 50, end="\r")
                print(f"\n🤖 Assistant: {response}")
                
                # Store in conversation history
                conversation_history.append({"role": "user", "content": user_input})
                conversation_history.append({"role": "assistant", "content": response})
                
                # Keep history manageable
                if len(conversation_history) > 20:
                    conversation_history = conversation_history[-20:]
                    
            except KeyboardInterrupt:
                print("\n\nGoodbye!")
                break
            except Exception as e:
                print(f"\nError: {e}")
    
    def start(self):
        """Start the terminal client"""
        self.running = True
        asyncio.run(self.chat_loop())

if __name__ == "__main__":
    client = TerminalClient()
    client.start()
