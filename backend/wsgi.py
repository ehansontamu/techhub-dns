# PythonAnywhere WSGI Configuration
# This file is used by PythonAnywhere to serve the Flask application

import sys
import os

# Add project to path
project_path = os.path.dirname(os.path.abspath(__file__))
if project_path not in sys.path:
    sys.path.insert(0, project_path)

# Set environment variables
os.environ['FLASK_ENV'] = 'production'

# Load environment variables from .env file if present
from dotenv import load_dotenv
env_path = os.path.join(project_path, '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)

# Import the Flask app
from app.main import app, socketio
application = app
