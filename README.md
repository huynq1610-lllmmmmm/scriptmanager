🚀 Script Manager
Script Manager is a lightweight, web-based application designed to manage, execute, and monitor local scripts through a clean and responsive user interface. Built with Python (Flask) and SQLite, it allows you to easily start, stop, and view real-time logs of various script types without needing to interact with the command line.

✨ Key Features
Web-Based Dashboard: Manage all your scripts from a clean, intuitive web UI.
Multi-Language Support: Execute scripts written in various languages automatically based on file extensions.
Process Management: Safely start and stop scripts. The app automatically terminates all running child processes upon shutdown.
Real-Time Logs: View live console outputs (stdout/stderr) of running scripts in a dedicated modal.
CRUD Operations: Easily add, edit, delete, and view script details stored securely in an SQLite database.
Cross-Platform: Works on Windows, Linux, and macOS (includes specific encoding fixes for Windows consoles).
Fully Responsive: Optimized for desktop, tablet, and mobile devices.
📂 Supported Script Types
The application automatically detects the file extension and runs the appropriate interpreter:

🐍 Python (.py)
🦇 Batch / Command (.bat, .cmd)
💙 PowerShell (.ps1)
🐧 Bash (.sh)
🟨 JavaScript (.js - requires Node.js)
💎 Ruby (.rb)
🐪 Perl (.pl)
🐘 PHP (.php)
🛠️ Tech Stack
Backend: Python, Flask, SQLite3
Frontend: HTML5, CSS3, Vanilla JavaScript (Jinja2 templating)
Process Management: subprocess, threading
⚙️ Installation & Usage
Prerequisites
Python 3.x installed on your system.
Required interpreters for the scripts you want to run (e.g., Node.js for .js, Ruby for .rb).
Steps
Clone this repository:
bash

git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name
Install required Python packages:
bash

pip install flask
Run the application:
bash

python script_manager_v4.py
Open your browser and navigate to:
http://localhost:7890

📝 Notes for Windows Users
The application automatically sets console encoding to UTF-8 to prevent Unicode errors.
For PowerShell scripts (.ps1), you might need to update your Execution Policy. Run PowerShell as Administrator and execute:
powershell script:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
