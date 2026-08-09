#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import sqlite3
import subprocess
import signal
import json
import threading
import time
import platform
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for
from werkzeug.serving import make_server
import atexit

# Thiết lập encoding cho Windows
if platform.system() == 'Windows':
    import locale
    import codecs
    
    # Set console encoding to UTF-8
    try:
        if sys.stdout.encoding != 'utf-8':
            sys.stdout.reconfigure(encoding='utf-8')
        if sys.stderr.encoding != 'utf-8':
            sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass
    
    # Set environment variables
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONUTF8'] = '1'

app = Flask(__name__)
app.secret_key = 'script_manager_secret_key'

# Global variables
running_processes = {}  # {script_id: process_object}
process_logs = {}       # {script_id: [log_lines]}
db_path = 'script_manager.db'

class ScriptManager:
    def __init__(self):
        self.init_database()
        self.load_scripts()
        
    def init_database(self):
        """Khởi tạo database SQLite"""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                path TEXT NOT NULL,
                status TEXT DEFAULT 'stopped',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_run TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def load_scripts(self):
        """Load scripts từ database khi khởi động"""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM scripts')
        scripts = cursor.fetchall()
        
        for script in scripts:
            script_id = script[0]
            process_logs[script_id] = []
            
        conn.close()
    
    def add_script(self, name, description, path):
        """Thêm script mới vào database"""
        if not os.path.exists(path):
            return False, "File không tồn tại"
        
        # Kiểm tra extension được hỗ trợ
        supported_extensions = ['.py', '.bat', '.cmd', '.ps1', '.sh', '.js', '.rb', '.pl', '.php']
        file_ext = os.path.splitext(path)[1].lower()
        
        if file_ext not in supported_extensions:
            return False, f"Loại file {file_ext} chưa được hỗ trợ. Các loại được hỗ trợ: {', '.join(supported_extensions)}"
        
        # Kiểm tra đặc biệt cho .bat files
        if file_ext == '.bat' and platform.system() == 'Windows':
            try:
                # Đọc vài dòng đầu để kiểm tra format
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    first_lines = f.readlines()[:5]
                    if first_lines:
                        print(f"DEBUG: .bat file content preview:")
                        for i, line in enumerate(first_lines):
                            print(f"  Line {i+1}: {repr(line.strip())}")
            except Exception as e:
                print(f"DEBUG: Không thể đọc file .bat: {e}")
            
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO scripts (name, description, path)
                VALUES (?, ?, ?)
            ''', (name, description, path))
            
            script_id = cursor.lastrowid
            process_logs[script_id] = []
            
            conn.commit()
            return True, f"Thêm script {file_ext} thành công"
        except sqlite3.IntegrityError:
            return False, "Tên script đã tồn tại"
        finally:
            conn.close()
    
    def get_all_scripts(self):
        """Lấy tất cả scripts từ database"""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM scripts ORDER BY created_at DESC')
        scripts = cursor.fetchall()
        
        conn.close()
        return scripts
    
    def get_script(self, script_id):
        """Lấy thông tin một script"""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM scripts WHERE id = ?', (script_id,))
        script = cursor.fetchone()
        
        conn.close()
        return script
    
    def update_script_status(self, script_id, status):
        """Cập nhật trạng thái script"""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE scripts 
            SET status = ?, last_run = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (status, script_id))
        
        conn.commit()
        conn.close()
    
    def _get_script_command(self, script_path):
        """Tạo command phù hợp dựa trên file extension"""
        file_ext = os.path.splitext(script_path)[1].lower()
        
        # Normalize path và quote nếu có spaces
        normalized_path = os.path.normpath(script_path)
        if ' ' in normalized_path:
            quoted_path = f'"{normalized_path}"'
        else:
            quoted_path = normalized_path
        
        if file_ext == '.py':
            return [sys.executable, '-u', script_path]
        elif file_ext == '.bat':
            if platform.system() == 'Windows':
                return f'"{normalized_path}"'  # Return as string for shell=True
            else:
                return ['wine', 'cmd', '/c', script_path]  # For Linux with Wine
        elif file_ext == '.cmd':
            return f'cmd /c "{normalized_path}"'
        elif file_ext == '.ps1':
            return f'powershell -ExecutionPolicy Bypass -File "{normalized_path}"'
        elif file_ext == '.sh':
            return ['bash', script_path]
        elif file_ext == '.js':
            return ['node', script_path]
        elif file_ext == '.rb':
            return ['ruby', script_path]
        elif file_ext == '.pl':
            return ['perl', script_path]
        elif file_ext == '.php':
            return ['php', script_path]
        else:
            # Fallback: cố gắng chạy trực tiếp (cho executable files)
            return [script_path]
    
    def start_script(self, script_id):
        """Khởi chạy script"""
        script = self.get_script(script_id)
        if not script:
            return False, "Script không tồn tại"
        
        if script_id in running_processes:
            return False, "Script đang chạy"
        
        script_path = script[3]  # path column
        
        try:
            # Thiết lập environment variables để xử lý Unicode
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUTF8'] = '1'
            
            # Lấy command phù hợp cho loại file
            command = self._get_script_command(script_path)
            file_ext = os.path.splitext(script_path)[1].lower()
            
            # Determine working directory
            work_dir = os.path.dirname(script_path) if os.path.dirname(script_path) else os.getcwd()
            
            # Khởi chạy process dựa trên loại command
            if isinstance(command, str):
                # Command là string (cho .bat, .cmd, .ps1)
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    encoding='utf-8',
                    errors='replace',
                    bufsize=1,
                    env=env,
                    shell=True,
                    cwd=work_dir
                )
            else:
                # Command là list (cho .py, .sh, .js, etc.)
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    encoding='utf-8',
                    errors='replace',
                    bufsize=1,
                    env=env,
                    cwd=work_dir
                )
            
            running_processes[script_id] = process
            process_logs[script_id] = []
            
            # Thêm log bắt đầu với thông tin command
            start_msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Khởi chạy {file_ext} script: {os.path.basename(script_path)}"
            start_msg += f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Working Directory: {work_dir}"
            if isinstance(command, str):
                start_msg += f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Command: {command}"
            else:
                start_msg += f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Command: {' '.join(command)}"
            self._add_log(script_id, start_msg)
            
            # Tạo thread để đọc output
            log_thread = threading.Thread(
                target=self._read_process_output,
                args=(script_id, process),
                daemon=True
            )
            log_thread.start()
            
            self.update_script_status(script_id, 'running')
            return True, f"Script {file_ext} đã được khởi chạy"
            
        except Exception as e:
            error_msg = f"Lỗi khởi chạy: {str(e)}"
            # Log chi tiết lỗi
            if script_id in process_logs:
                self._add_log(script_id, f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: {error_msg}")
            return False, error_msg
    
    def stop_script(self, script_id):
        """Dừng script"""
        if script_id not in running_processes:
            return False, "Script không đang chạy"
        
        process = running_processes[script_id]
        
        try:
            # Gửi signal SIGTERM
            process.terminate()
            
            # Đợi process kết thúc (timeout 5s)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Nếu không dừng, force kill
                process.kill()
                process.wait()
            
            del running_processes[script_id]
            self.update_script_status(script_id, 'stopped')
            
            # Thêm log dừng
            self._add_log(script_id, f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Script đã dừng")
            
            return True, "Script đã dừng"
            
        except Exception as e:
            return False, f"Lỗi dừng script: {str(e)}"
    
    def get_script_status(self, script_id):
        """Lấy trạng thái script"""
        if script_id in running_processes:
            process = running_processes[script_id]
            if process.poll() is None:
                return "running"
            else:
                # Process đã kết thúc
                del running_processes[script_id]
                self.update_script_status(script_id, 'stopped')
                return "stopped"
        return "stopped"
    
    def get_script_logs(self, script_id, last_n=50):
        """Lấy logs của script"""
        logs = process_logs.get(script_id, [])
        return logs[-last_n:] if logs else []
    
    def update_script(self, script_id, name, description, path):
        """Cập nhật thông tin script"""
        if not os.path.exists(path):
            return False, "File không tồn tại"
        
        # Kiểm tra extension được hỗ trợ
        supported_extensions = ['.py', '.bat', '.cmd', '.ps1', '.sh', '.js', '.rb', '.pl', '.php']
        file_ext = os.path.splitext(path)[1].lower()
        
        if file_ext not in supported_extensions:
            return False, f"Loại file {file_ext} chưa được hỗ trợ. Các loại được hỗ trợ: {', '.join(supported_extensions)}"
            
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        try:
            # Kiểm tra tên script đã tồn tại chưa (ngoại trừ script hiện tại)
            cursor.execute('SELECT id FROM scripts WHERE name = ? AND id != ?', (name, script_id))
            if cursor.fetchone():
                return False, "Tên script đã tồn tại"
            
            cursor.execute('''
                UPDATE scripts 
                SET name = ?, description = ?, path = ?
                WHERE id = ?
            ''', (name, description, path, script_id))
            
            conn.commit()
            return True, f"Cập nhật script {file_ext} thành công"
        except Exception as e:
            return False, f"Lỗi cập nhật: {str(e)}"
        finally:
            conn.close()
    
    def delete_script(self, script_id):
        """Xóa script"""
        # Dừng script nếu đang chạy
        if script_id in running_processes:
            self.stop_script(script_id)
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('DELETE FROM scripts WHERE id = ?', (script_id,))
            
            if cursor.rowcount == 0:
                return False, "Script không tồn tại"
            
            # Xóa logs
            if script_id in process_logs:
                del process_logs[script_id]
            
            conn.commit()
            return True, "Xóa script thành công"
        except Exception as e:
            return False, f"Lỗi xóa script: {str(e)}"
        finally:
            conn.close()
    
    def _read_process_output(self, script_id, process):
        """Đọc output từ process (chạy trong thread riêng)"""
        try:
            for line in iter(process.stdout.readline, ''):
                if line:
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    # Xử lý encoding an toàn
                    try:
                        clean_line = line.strip()
                        # Loại bỏ các ký tự control characters
                        clean_line = ''.join(char for char in clean_line if ord(char) >= 32 or char in '\t\n\r')
                        log_line = f"[{timestamp}] {clean_line}"
                    except UnicodeDecodeError:
                        # Fallback nếu có lỗi encoding
                        log_line = f"[{timestamp}] [ENCODING ERROR] {repr(line.strip())}"
                    except Exception as e:
                        log_line = f"[{timestamp}] [LOG ERROR] {str(e)}"
                    
                    self._add_log(script_id, log_line)
                    
                # Kiểm tra process còn chạy không
                if process.poll() is not None:
                    break
                    
        except Exception as e:
            error_msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [READER ERROR] {str(e)}"
            self._add_log(script_id, error_msg)
        finally:
            # Process kết thúc
            if script_id in running_processes:
                del running_processes[script_id]
                self.update_script_status(script_id, 'stopped')
                
                # Thêm log kết thúc
                end_msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Script đã kết thúc"
                self._add_log(script_id, end_msg)
    
    def _add_log(self, script_id, log_line):
        """Thêm log line"""
        if script_id not in process_logs:
            process_logs[script_id] = []
        
        process_logs[script_id].append(log_line)
        
        # Giới hạn số dòng log (keep only last 200 lines)
        if len(process_logs[script_id]) > 200:
            process_logs[script_id] = process_logs[script_id][-200:]
    
    def stop_all_scripts(self):
        """Dừng tất cả scripts khi đóng ứng dụng"""
        for script_id in list(running_processes.keys()):
            self.stop_script(script_id)

# Khởi tạo ScriptManager
script_manager = ScriptManager()

@app.template_filter('file_icon')
def file_icon_filter(file_path):
    """Lấy icon cho file dựa trên extension"""
    ext = os.path.splitext(file_path)[1].lower()
    icons = {
        '.py': '🐍',
        '.bat': '🦇',
        '.cmd': '⚡',
        '.ps1': '💙',
        '.sh': '🐧',
        '.js': '🟨',
        '.rb': '💎',
        '.pl': '🐪',
        '.php': '🐘'
    }
    return icons.get(ext, '📄')

@app.template_filter('file_type')
def file_type_filter(file_path):
    """Lấy tên loại file"""
    ext = os.path.splitext(file_path)[1].lower()
    types = {
        '.py': 'Python',
        '.bat': 'Batch',
        '.cmd': 'Command',
        '.ps1': 'PowerShell',
        '.sh': 'Bash',
        '.js': 'JavaScript',
        '.rb': 'Ruby',
        '.pl': 'Perl',
        '.php': 'PHP'
    }
    return types.get(ext, 'Unknown')

@app.route('/')
def index():
    """Trang chính"""
    scripts = script_manager.get_all_scripts()
    return render_template('index.html', scripts=scripts)

@app.route('/add_script', methods=['POST'])
def add_script():
    """API thêm script mới"""
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    path = request.form.get('path', '').strip()
    
    if not name or not path:
        return jsonify({'success': False, 'message': 'Vui lòng điền đầy đủ thông tin'})
    
    success, message = script_manager.add_script(name, description, path)
    return jsonify({'success': success, 'message': message})

@app.route('/test_script/<int:script_id>')
def test_script(script_id):
    """API test script để kiểm tra trước khi chạy"""
    script = script_manager.get_script(script_id)
    if not script:
        return jsonify({'success': False, 'message': 'Script không tồn tại'})
    
    script_path = script[3]
    file_ext = os.path.splitext(script_path)[1].lower()
    
    test_info = {
        'path_exists': os.path.exists(script_path),
        'file_size': os.path.getsize(script_path) if os.path.exists(script_path) else 0,
        'working_dir': os.path.dirname(script_path) if os.path.dirname(script_path) else os.getcwd(),
        'command': None,
        'file_type': file_ext,
        'platform': platform.system()
    }
    
    try:
        command = script_manager._get_script_command(script_path)
        if isinstance(command, str):
            test_info['command'] = command
        else:
            test_info['command'] = ' '.join(command)
            
        # Đặc biệt cho .bat files
        if file_ext == '.bat':
            try:
                with open(script_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(500)  # Đọc 500 ký tự đầu
                    test_info['file_preview'] = content
            except:
                test_info['file_preview'] = "Không thể đọc nội dung file"
                
    except Exception as e:
        test_info['error'] = str(e)
    
    return jsonify({'success': True, 'test_info': test_info})

@app.route('/start_script/<int:script_id>')
def start_script(script_id):
    """API khởi chạy script"""
    success, message = script_manager.start_script(script_id)
    return jsonify({'success': success, 'message': message})

@app.route('/stop_script/<int:script_id>')
def stop_script(script_id):
    """API dừng script"""
    success, message = script_manager.stop_script(script_id)
    return jsonify({'success': success, 'message': message})

@app.route('/script_status/<int:script_id>')
def get_script_status(script_id):
    """API lấy trạng thái script"""
    status = script_manager.get_script_status(script_id)
    return jsonify({'status': status})

@app.route('/script_logs/<int:script_id>')
def get_script_logs(script_id):
    """API lấy logs script"""
    logs = script_manager.get_script_logs(script_id)
    return jsonify({'logs': logs})

@app.route('/get_script/<int:script_id>')
def get_script_info(script_id):
    """API lấy thông tin script để edit"""
    script = script_manager.get_script(script_id)
    if script:
        return jsonify({
            'success': True,
            'script': {
                'id': script[0],
                'name': script[1],
                'description': script[2],
                'path': script[3]
            }
        })
    return jsonify({'success': False, 'message': 'Script không tồn tại'})

@app.route('/update_script/<int:script_id>', methods=['POST'])
def update_script(script_id):
    """API cập nhật script"""
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    path = request.form.get('path', '').strip()
    
    if not name or not path:
        return jsonify({'success': False, 'message': 'Vui lòng điền đầy đủ thông tin'})
    
    success, message = script_manager.update_script(script_id, name, description, path)
    return jsonify({'success': success, 'message': message})

@app.route('/delete_script/<int:script_id>', methods=['POST'])
def delete_script(script_id):
    """API xóa script"""
    success, message = script_manager.delete_script(script_id)
    return jsonify({'success': success, 'message': message})

def cleanup():
    """Dọn dẹp khi đóng ứng dụng"""
    print("Đang dừng tất cả scripts...")
    script_manager.stop_all_scripts()
    print("Đã dừng tất cả scripts.")

# Đăng ký cleanup function
atexit.register(cleanup)

# HTML Template với CSS đã được sửa
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Script Manager</title>
    <style>
        * {
            box-sizing: border-box;
        }
        
        body {
            font-family: Arial, sans-serif;
            margin: 10px;
            background-color: #f5f5f5;
            font-size: 14px;
            line-height: 1.4;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        
        h1 {
            color: #333;
            text-align: center;
            font-size: 28px;
            margin-bottom: 25px;
            border-bottom: 3px solid #007bff;
            padding-bottom: 10px;
        }
        
        h2 {
            font-size: 20px;
            margin-bottom: 15px;
            color: #555;
        }
        
        .add-section {
            border: 2px solid #007bff;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 25px;
            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
        }
        
        .form-group {
            margin-bottom: 15px;
        }
        
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
            font-size: 14px;
            color: #333;
        }
        
        input[type="text"], textarea {
            width: 100%;
            padding: 12px;
            border: 2px solid #ddd;
            border-radius: 6px;
            font-size: 14px;
            transition: border-color 0.3s ease;
        }
        
        input[type="text"]:focus, textarea:focus {
            outline: none;
            border-color: #007bff;
            box-shadow: 0 0 0 3px rgba(0, 123, 255, 0.1);
        }
        
        textarea {
            height: 80px;
            resize: vertical;
        }
        
        .btn {
            padding: 10px 16px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            margin: 3px;
            font-size: 13px;
            font-weight: 500;
            min-width: 80px;
            transition: all 0.3s ease;
            display: inline-block;
            text-align: center;
        }
        
        .btn-primary {
            background-color: #007bff;
            color: white;
        }
        
        .btn-success {
            background-color: #28a745;
            color: white;
        }
        
        .btn-danger {
            background-color: #dc3545;
            color: white;
        }
        
        .btn-info {
            background-color: #17a2b8;
            color: white;
        }
        
        .btn-warning {
            background-color: #ffc107;
            color: #212529;
        }
        
        .btn-secondary {
            background-color: #6c757d;
            color: white;
        }
        
        .btn:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.2);
        }
        
        .script-item {
            border: 2px solid #e9ecef;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            background-color: white;
            transition: all 0.3s ease;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .script-item:hover {
            border-color: #007bff;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        
        .script-header {
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 20px;
            align-items: start;
            margin-bottom: 15px;
        }
        
        .script-info {
            min-width: 0; /* Cho phép shrink */
        }
        
        .script-info h3 {
            margin: 0 0 8px 0;
            color: #333;
            font-size: 18px;
            font-weight: 600;
        }
        
        .script-info p {
            margin: 6px 0;
            color: #666;
            font-size: 13px;
            word-break: break-all;
        }
        
        .script-actions {
            display: flex;
            flex-direction: column;
            gap: 8px;
            align-items: flex-end;
            min-width: 0;
        }
        
        .status-and-buttons {
            display: flex;
            flex-direction: column;
            gap: 10px;
            align-items: flex-end;
        }
        
        .button-group {
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
            justify-content: flex-end;
        }
        
        .status {
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: bold;
            text-align: center;
            min-width: 80px;
        }
        
        .status-running {
            background-color: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        
        .status-stopped {
            background-color: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        
        .log-modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.5);
            overflow: auto;
        }
        
        .log-content {
            background-color: #fefefe;
            margin: 2% auto;
            padding: 20px;
            border-radius: 10px;
            width: 95%;
            max-width: 900px;
            height: 85%;
            display: flex;
            flex-direction: column;
            max-height: 90vh;
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
        }
        
        .log-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #eee;
        }
        
        .close {
            color: #aaa;
            font-size: 28px;
            font-weight: bold;
            cursor: pointer;
            transition: color 0.3s ease;
        }
        
        .close:hover {
            color: #333;
        }
        
        .log-output {
            flex: 1;
            background-color: #1a1a1a;
            color: #00ff00;
            padding: 15px;
            border-radius: 6px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            white-space: pre-wrap;
            line-height: 1.4;
        }
        
        .message {
            padding: 12px;
            margin: 10px 0;
            border-radius: 6px;
            font-weight: 500;
        }
        
        .message.success {
            background-color: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        
        .message.error {
            background-color: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        
        /* ========== RESPONSIVE DESIGN ========== */
        
        /* Tablet */
        @media (max-width: 768px) {
            body {
                margin: 5px;
                font-size: 13px;
            }
            
            .container {
                padding: 15px;
            }
            
            h1 {
                font-size: 24px;
                margin-bottom: 20px;
            }
            
            .script-header {
                grid-template-columns: 1fr;
                gap: 15px;
            }
            
            .script-actions {
                align-items: stretch;
            }
            
            .status-and-buttons {
                align-items: stretch;
            }
            
            .button-group {
                justify-content: stretch;
            }
            
            .btn {
                flex: 1;
                min-width: 0;
            }
            
            .log-content {
                width: 98%;
                height: 90%;
                margin: 1% auto;
                padding: 15px;
            }
        }
        
        /* Mobile */
        @media (max-width: 480px) {
            body {
                margin: 2px;
                font-size: 12px;
            }
            
            .container {
                padding: 10px;
                border-radius: 4px;
            }
            
            h1 {
                font-size: 20px;
                margin-bottom: 15px;
            }
            
            h2 {
                font-size: 16px;
                margin-bottom: 12px;
            }
            
            .add-section {
                padding: 15px;
                margin-bottom: 15px;
            }
            
            .script-item {
                padding: 15px;
                margin-bottom: 15px;
            }
            
            .script-info h3 {
                font-size: 16px;
                margin-bottom: 6px;
            }
            
            .script-info p {
                font-size: 11px;
                margin: 4px 0;
            }
            
            .button-group {
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 8px;
            }
            
            .btn {
                padding: 12px 8px;
                font-size: 11px;
                min-width: 0;
            }
            
            .status {
                font-size: 10px;
                padding: 4px 8px;
                margin-bottom: 8px;
            }
            
            .log-content {
                width: 100%;
                height: 100%;
                margin: 0;
                border-radius: 0;
                padding: 10px;
            }
            
            .log-output {
                font-size: 10px;
                padding: 10px;
            }
        }
        
        /* Very small screens */
        @media (max-width: 320px) {
            .container {
                padding: 8px;
            }
            
            h1 {
                font-size: 18px;
            }
            
            .script-info h3 {
                font-size: 14px;
            }
            
            .script-info p {
                font-size: 10px;
            }
            
            .btn {
                font-size: 10px;
                padding: 10px 6px;
            }
            
            input[type="text"], textarea {
                font-size: 12px;
                padding: 8px;
            }
        }
        
        /* Print styles */
        @media print {
            .script-actions, .add-section {
                display: none;
            }
            
            .script-item {
                break-inside: avoid;
                border: 1px solid #333;
                margin-bottom: 10px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 Script Manager</h1>
        
        <!-- Add New Script Section -->
        <div class="add-section">
            <h2>➕ Thêm Script Mới</h2>
            <form id="addScriptForm">
                <div class="form-group">
                    <label for="scriptName">Tên Script:</label>
                    <input type="text" id="scriptName" name="name" required>
                </div>
                <div class="form-group">
                    <label for="scriptDescription">Mô tả:</label>
                    <textarea id="scriptDescription" name="description" placeholder="Nhập mô tả chi tiết về script..."></textarea>
                </div>
                <div class="form-group">
                    <label for="scriptPath">Đường dẫn Script:</label>
                    <input type="text" id="scriptPath" name="path" required placeholder="VD: E:\\script.bat, C:\\script.ps1, /path/script.py">
                    <small style="color: #666; font-size: 11px; margin-top: 5px; display: block;">
                        📋 Các loại file được hỗ trợ: <strong>.py, .bat, .cmd, .ps1, .sh, .js, .rb, .pl, .php</strong>
                    </small>
                </div>
                <button type="submit" class="btn btn-primary">💾 Lưu Script</button>
            </form>
        </div>
        
        <!-- Scripts List -->
        <div id="scriptsList">
            <h2>📋 Danh sách Scripts</h2>
            {% if scripts %}
                {% for script in scripts %}
                <div class="script-item" data-script-id="{{ script[0] }}">
                    <div class="script-header">
                        <div class="script-info">
                            <h3>{{ script[3]|file_icon }} {{ script[1] }} <span style="font-size: 12px; color: #666;">({{ script[3]|file_type }})</span></h3>
                            <p><strong>Mô tả:</strong> {{ script[2] or 'Không có mô tả' }}</p>
                            <p><strong>Đường dẫn:</strong> <code>{{ script[3] }}</code></p>
                            <p><strong>Tạo lúc:</strong> {{ script[5] }}</p>
                        </div>
                        <div class="script-actions">
                            <div class="status-and-buttons">
                                <span class="status status-{{ script[4] }}" id="status-{{ script[0] }}">
                                    {{ script[4].upper() }}
                                </span>
                                <div class="button-group">
                                    <button class="btn btn-success" onclick="startScript({{ script[0] }})">▶️ Start</button>
                                    <button class="btn btn-danger" onclick="stopScript({{ script[0] }})">⏹️ Stop</button>
                                    <button class="btn btn-info" onclick="showLogs({{ script[0] }}, '{{ script[1] }}', '{{ script[3] }}')">📄 Logs</button>
                                    <button class="btn btn-warning" onclick="editScript({{ script[0] }})">✏️ Edit</button>
                                    <button class="btn btn-danger" onclick="deleteScript({{ script[0] }}, '{{ script[1] }}')">🗑️ Delete</button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <div style="text-align: center; padding: 40px; color: #666;">
                    <h3>📝 Chưa có script nào được thêm</h3>
                    <p>Hãy thêm script đầu tiên bằng form ở trên!</p>
                </div>
            {% endif %}
        </div>
    </div>
    
    <!-- Log Modal -->
    <div id="logModal" class="log-modal">
        <div class="log-content">
            <div class="log-header">
                <h3 id="logTitle">Script Logs</h3>
                <span class="close" onclick="closeLogs()">&times;</span>
            </div>
            <div id="logOutput" class="log-output"></div>
            <button class="btn btn-info" onclick="refreshLogs()">🔄 Làm mới</button>
        </div>
    </div>
    
    <!-- Edit Script Modal -->
    <div id="editModal" class="log-modal">
        <div class="log-content" style="height: auto; max-height: 80%;">
            <div class="log-header">
                <h3>✏️ Chỉnh sửa Script</h3>
                <span class="close" onclick="closeEditModal()">&times;</span>
            </div>
            <form id="editScriptForm" style="flex: 1;">
                <input type="hidden" id="editScriptId">
                <div class="form-group">
                    <label for="editScriptName">Tên Script:</label>
                    <input type="text" id="editScriptName" name="name" required>
                </div>
                <div class="form-group">
                    <label for="editScriptDescription">Mô tả:</label>
                    <textarea id="editScriptDescription" name="description"></textarea>
                </div>
                <div class="form-group">
                    <label for="editScriptPath">Đường dẫn Script:</label>
                    <input type="text" id="editScriptPath" name="path" required>
                    <small style="color: #666; font-size: 11px; margin-top: 5px; display: block;">
                        📋 Các loại file được hỗ trợ: <strong>.py, .bat, .cmd, .ps1, .sh, .js, .rb, .pl, .php</strong>
                    </small>
                </div>
                <div style="margin-top: 20px;">
                    <button type="submit" class="btn btn-primary">💾 Cập nhật</button>
                    <button type="button" class="btn btn-secondary" onclick="closeEditModal()">❌ Hủy</button>
                </div>
            </form>
        </div>
    </div>
    
    <!-- Message Area -->
    <div id="messageArea"></div>

    <script>
        let currentLogScript = null;
        let logInterval = null;
        
        // Add script form handler
        document.getElementById('addScriptForm').addEventListener('submit', function(e) {
            e.preventDefault();
            
            const formData = new FormData(this);
            
            fetch('/add_script', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                showMessage(data.message, data.success ? 'success' : 'error');
                if (data.success) {
                    this.reset();
                    setTimeout(() => location.reload(), 1000);
                }
            })
            .catch(error => {
                showMessage('Lỗi: ' + error.message, 'error');
            });
        });
        
        // Edit script form handler
        document.getElementById('editScriptForm').addEventListener('submit', function(e) {
            e.preventDefault();
            
            const scriptId = document.getElementById('editScriptId').value;
            const formData = new FormData(this);
            
            fetch(`/update_script/${scriptId}`, {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                showMessage(data.message, data.success ? 'success' : 'error');
                if (data.success) {
                    closeEditModal();
                    setTimeout(() => location.reload(), 1000);
                }
            })
            .catch(error => {
                showMessage('Lỗi: ' + error.message, 'error');
            });
        });
        
        function startScript(scriptId) {
            fetch(`/start_script/${scriptId}`)
            .then(response => response.json())
            .then(data => {
                showMessage(data.message, data.success ? 'success' : 'error');
                if (data.success) {
                    updateScriptStatus(scriptId);
                }
            })
            .catch(error => {
                showMessage('Lỗi: ' + error.message, 'error');
            });
        }
        
        function stopScript(scriptId) {
            fetch(`/stop_script/${scriptId}`)
            .then(response => response.json())
            .then(data => {
                showMessage(data.message, data.success ? 'success' : 'error');
                if (data.success) {
                    updateScriptStatus(scriptId);
                }
            })
            .catch(error => {
                showMessage('Lỗi: ' + error.message, 'error');
            });
        }
        
        function updateScriptStatus(scriptId) {
            fetch(`/script_status/${scriptId}`)
            .then(response => response.json())
            .then(data => {
                const statusElement = document.getElementById(`status-${scriptId}`);
                if (statusElement) {
                    statusElement.textContent = data.status.toUpperCase();
                    statusElement.className = `status status-${data.status}`;
                }
            })
            .catch(error => console.error('Error updating status:', error));
        }
        
        function showLogs(scriptId, scriptName, scriptPath) {
            currentLogScript = scriptId;
            
            // Hiển thị icon dựa trên file extension
            if (scriptPath) {
                const ext = scriptPath.split('.').pop().toLowerCase();
                const icons = {
                    'py': '🐍', 'bat': '🦇', 'cmd': '⚡', 'ps1': '💙', 
                    'sh': '🐧', 'js': '🟨', 'rb': '💎', 'pl': '🐪', 'php': '🐘'
                };
                const icon = icons[ext] || '📄';
                document.getElementById('logTitle').textContent = `${icon} Logs - ${scriptName}`;
            } else {
                document.getElementById('logTitle').textContent = `📄 Logs - ${scriptName}`;
            }
            
            document.getElementById('logModal').style.display = 'block';
            
            // Prevent body scroll on mobile when modal is open
            if (window.innerWidth <= 768) {
                document.body.style.overflow = 'hidden';
            }
            
            refreshLogs();
            
            // Auto refresh logs every 2 seconds
            logInterval = setInterval(refreshLogs, 2000);
        }
        
        function refreshLogs() {
            if (!currentLogScript) return;
            
            fetch(`/script_logs/${currentLogScript}`)
            .then(response => response.json())
            .then(data => {
                const logOutput = document.getElementById('logOutput');
                logOutput.textContent = data.logs.join('\\n');
                logOutput.scrollTop = logOutput.scrollHeight;
            })
            .catch(error => console.error('Error loading logs:', error));
        }
        
        function closeLogs() {
            document.getElementById('logModal').style.display = 'none';
            
            // Restore body scroll on mobile
            document.body.style.overflow = 'auto';
            
            if (logInterval) {
                clearInterval(logInterval);
                logInterval = null;
            }
            currentLogScript = null;
        }
        
        function closeEditModal() {
            document.getElementById('editModal').style.display = 'none';
            
            // Restore body scroll on mobile
            document.body.style.overflow = 'auto';
        }
        
        function deleteScript(scriptId, scriptName) {
            if (confirm(`Bạn có chắc chắn muốn xóa script "${scriptName}"?\\n\\nScript sẽ bị dừng nếu đang chạy và xóa vĩnh viễn khỏi hệ thống.`)) {
                fetch(`/delete_script/${scriptId}`, {
                    method: 'POST'
                })
                .then(response => response.json())
                .then(data => {
                    showMessage(data.message, data.success ? 'success' : 'error');
                    if (data.success) {
                        setTimeout(() => location.reload(), 1000);
                    }
                })
                .catch(error => {
                    showMessage('Lỗi: ' + error.message, 'error');
                });
            }
        }
        
        function showMessage(message, type) {
            const messageArea = document.getElementById('messageArea');
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${type}`;
            messageDiv.textContent = message;
            
            messageArea.appendChild(messageDiv);
            
            // Scroll to message on mobile
            if (window.innerWidth <= 768) {
                messageDiv.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
            
            setTimeout(() => {
                messageDiv.remove();
            }, 5000);
        }
        
        // Update all script statuses every 5 seconds
        setInterval(() => {
            const scriptItems = document.querySelectorAll('.script-item');
            scriptItems.forEach(item => {
                const scriptId = item.getAttribute('data-script-id');
                updateScriptStatus(scriptId);
            });
        }, 5000);
        
        // Close modal when clicking outside
        window.onclick = function(event) {
            const logModal = document.getElementById('logModal');
            const editModal = document.getElementById('editModal');
            
            if (event.target == logModal) {
                closeLogs();
            }
            if (event.target == editModal) {
                closeEditModal();
            }
        }
        
        // Touch support for mobile
        let touchStartY = 0;
        
        document.addEventListener('touchstart', function(e) {
            touchStartY = e.touches[0].clientY;
        });
        
        // Keyboard support for accessibility
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                const logModal = document.getElementById('logModal');
                const editModal = document.getElementById('editModal');
                
                if (logModal.style.display === 'block') {
                    closeLogs();
                }
                if (editModal.style.display === 'block') {
                    closeEditModal();
                }
            }
        });
        
        // Auto-focus first input in modals for better UX
        function editScript(scriptId) {
            fetch(`/get_script/${scriptId}`)
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    const script = data.script;
                    document.getElementById('editScriptId').value = script.id;
                    document.getElementById('editScriptName').value = script.name;
                    document.getElementById('editScriptDescription').value = script.description || '';
                    document.getElementById('editScriptPath').value = script.path;
                    document.getElementById('editModal').style.display = 'block';
                    
                    // Prevent body scroll on mobile when modal is open
                    if (window.innerWidth <= 768) {
                        document.body.style.overflow = 'hidden';
                    }
                    
                    // Focus first input for better UX
                    setTimeout(() => {
                        document.getElementById('editScriptName').focus();
                    }, 100);
                } else {
                    showMessage(data.message, 'error');
                }
            })
            .catch(error => {
                showMessage('Lỗi: ' + error.message, 'error');
            });
        }
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    # Tạo templates directory nếu chưa có
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    # Ghi HTML template vào file
    with open('templates/index.html', 'w', encoding='utf-8') as f:
        f.write(HTML_TEMPLATE)
    
    try:
        print("🚀 Khởi động Script Manager...")
        print("📍 Ứng dụng đang chạy tại: http://localhost:7890")
        print("✨ Tính năng có sẵn:")
        print("   • ➕ Thêm script mới")
        print("   • ▶️ Start/Stop scripts")
        print("   • 📄 Xem logs real-time")
        print("   • ✏️ Chỉnh sửa scripts")
        print("   • 🗑️ Xóa scripts")
        print("📋 Các loại file được hỗ trợ:")
        print("   • 🐍 Python (.py)")
        print("   • 🦇 Batch (.bat, .cmd)")
        print("   • 💙 PowerShell (.ps1)")
        print("   • 🐧 Bash (.sh)")
        print("   • 🟨 JavaScript (.js)")
        print("   • 💎 Ruby (.rb)")
        print("   • 🐪 Perl (.pl)")
        print("   • 🐘 PHP (.php)")
        print("📱 Responsive Design:")
        print("   • 💻 Desktop (1200px+)")
        print("   • 📱 Tablet (768px-1199px)")
        print("   • 📞 Mobile (320px-767px)")
        print("   • ⌨️ Keyboard navigation (ESC to close)")
        
        # Hiển thị thông tin encoding
        print(f"🔤 System encoding: {sys.stdout.encoding}")
        print(f"🖥️ Platform: {platform.system()}")
        
        
        print("⚠️ Lưu ý: Nếu gặp lỗi Unicode trên Windows:")
        print("   1. Đảm bảo script được lưu với encoding UTF-8")
        print("   2. Sử dụng function safe_print() trong script")
        print("   3. Thiết lập PYTHONIOENCODING=utf-8")
        print("💡 PowerShell scripts (.ps1):")
        print("   • Cần đặt ExecutionPolicy phù hợp")
        print("   • VD: Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser")
        print("⏹️  Nhấn Ctrl+C để dừng ứng dụng")
        
        # Chạy Flask app
        app.run(host='0.0.0.0', port=7890, debug=False)
        
    except KeyboardInterrupt:
        print("\n🛑 Đang dừng ứng dụng...")
        cleanup()
        print("✅ Ứng dụng đã dừng.")
    except Exception as e:
        print(f"❌ Lỗi: {e}")
        cleanup()
