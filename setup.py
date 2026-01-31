#!/usr/bin/env python3
import os
import sys
import shutil

def setup_project():
    """Setup the project structure"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    print("🔧 Setting up project structure...")
    
    # Create necessary directories
    directories = ['services', 'platforms', 'utils', 'commands']
    for directory in directories:
        dir_path = os.path.join(current_dir, directory)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
            print(f"✅ Created directory: {directory}")
        
        # Create __init__.py if it doesn't exist
        init_file = os.path.join(dir_path, '__init__.py')
        if not os.path.exists(init_file):
            with open(init_file, 'w') as f:
                f.write('# Package initializer\n')
            print(f"✅ Created __init__.py in {directory}")
    
    # Check commands files
    commands_files = ['__init__.py', 'base.py', 'admin.py', 'chat.py',
                      'ha.py', 'model.py', 'persona.py']
    
    commands_dir = os.path.join(current_dir, 'commands')
    for file in commands_files:
        file_path = os.path.join(commands_dir, file)
        if not os.path.exists(file_path):
            if file == '__init__.py':
                content = '''# commands/__init__.py

def register_all_commands(client):
    """Register all command modules with Discord client"""
    print("🔧 Registering commands...")
    
    try:
        # Dynamically import each module
        from .admin import AdminCommands
        from .ha import HACommands
        from .chat import ChatCommands
        from .persona import PersonaCommands
        from .model import ModelCommands
        
        modules = [
            AdminCommands(client),
            HACommands(client),
            ChatCommands(client),
            PersonaCommands(client),
            ModelCommands(client),
        ]
        
        for module in modules:
            module.register()
        
        print(f"✅ Registered commands from {len(modules)} modules")
        return modules
        
    except ImportError as e:
        print(f"❌ Error importing command modules: {e}")
        return []

# Export the function
__all__ = ['register_all_commands']'''
            elif file == 'base.py':
                content = '''# commands/base.py
from whitelist import get_user_permission

class CommandBase:
    def __init__(self, client):
        self.client = client

    def register(self):
        raise NotImplementedError("Subclasses must implement register()")
'''
            else:
                content = f'''# commands/{file}
import discord
from discord import app_commands
from .base import CommandBase

class {file.replace('.py', '').title().replace('_', '')}Commands(CommandBase):
    def register(self):
        # Add commands here
        pass'''
            
            with open(file_path, 'w') as f:
                f.write(content)
            print(f"✅ Created {file} in commands directory")
    
    print("\n✅ Setup complete!")
    print("\nRun: python main.py")

if __name__ == "__main__":
    setup_project()
