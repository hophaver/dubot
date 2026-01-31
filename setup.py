#!/usr/bin/env python3
"""Create project directories and command package structure. Safe to run on existing project."""
import os


def setup_project():
    """Create necessary directories and command packages (only if missing)."""
    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)

    print("🔧 Setting up project structure...")

    # Top-level directories
    for name in ("services", "platforms", "utils", "commands", "data", "web", "scripts"):
        path = os.path.join(root, name)
        if not os.path.exists(path):
            os.makedirs(path)
            print(f"  ✅ Created directory: {name}/")

    # commands/ packages (must match main.py imports)
    command_packages = (
        "general", "file", "chat", "reminder", "persona", "model",
        "download", "translate", "scripts", "admin", "ha", "help",
    )
    commands_dir = os.path.join(root, "commands")
    for pkg in command_packages:
        pkg_dir = os.path.join(commands_dir, pkg)
        if not os.path.exists(pkg_dir):
            os.makedirs(pkg_dir)
            print(f"  ✅ Created directory: commands/{pkg}/")
        init_path = os.path.join(pkg_dir, "__init__.py")
        if not os.path.exists(init_path):
            with open(init_path, "w") as f:
                f.write("")
            print(f"  ✅ Created commands/{pkg}/__init__.py")

    # commands/__init__.py (only if missing)
    commands_init = os.path.join(commands_dir, "__init__.py")
    if not os.path.exists(commands_init):
        with open(commands_init, "w") as f:
            f.write('"""Command packages: general, file, chat, reminder, persona, model, download, translate, scripts, admin, ha, help."""\n')
        print("  ✅ Created commands/__init__.py")

    # services, platforms, utils __init__.py (only if missing)
    for name in ("services", "platforms", "utils"):
        init_path = os.path.join(root, name, "__init__.py")
        if not os.path.exists(init_path):
            with open(init_path, "w") as f:
                f.write("")
            print(f"  ✅ Created {name}/__init__.py")

    print("\n✅ Setup complete. Run: python main.py")


if __name__ == "__main__":
    setup_project()
