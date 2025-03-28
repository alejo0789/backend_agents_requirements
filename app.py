from flask import Flask, request, jsonify, session
from flask_cors import CORS
import google.generativeai as genai
import os
from dotenv import load_dotenv
import re
import logging
import base64
from datetime import datetime, timedelta
import threading
import time
import uuid
# Import our custom modules
from jobs import JobManager
from claude_service import ClaudeService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables first, before any other code
load_dotenv()
app = Flask(__name__)

# Add session configuration for cross-domain cookies
app.config.update(
    SESSION_COOKIE_SAMESITE='None',     # Allows cross-domain cookies
    SESSION_COOKIE_SECURE=True,         # For HTTPS connections
    SESSION_COOKIE_HTTPONLY=True,       # Prevents JavaScript access to the cookie
    SESSION_TYPE='filesystem',          # Use filesystem instead of signing cookies
    SESSION_PERMANENT=True,             # Make sessions persistent
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24)  # Set session lifetime
)

# Make all sessions permanent by default
@app.before_request
def make_session_permanent():
    session.permanent = True
    app.permanent_session_lifetime = timedelta(hours=24)
    
    # Debug session information
    if request.endpoint != 'static':
        logger.info(f"Session ID: {session.get('session_id', 'None')}")
        logger.info(f"Request path: {request.path}")
        logger.info(f"Session contains messages: {'messages' in session}")

# Configure CORS to allow requests from your frontend
CORS(app, 
     supports_credentials=True, 
     origins=['http://localhost:3000', 'https://agnets-requirements-pib3.vercel.app'],
     methods=['GET', 'POST', 'OPTIONS'],  # Explicitly state allowed methods
     allow_headers=['Content-Type', 'Authorization'],  # Allow necessary headers
     max_age=3600  # Cache preflight requests for 1 hour
)

@app.after_request
def add_cors_headers(response):
    # Ensure your production domain is in the allowed origins
    frontend_url = 'https://agnets-requirements-pib3.vercel.app'
    origin = request.headers.get('Origin')
    
    # If the request came from your frontend
    if origin and (origin == frontend_url or origin == 'http://localhost:3000'):
        response.headers.add('Access-Control-Allow-Origin', origin)
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
    
    return response

# Set a strong secret key
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

# Using Gemini 1.5 Pro for better context handling
GEMINI_MODEL = "models/gemini-1.5-pro" 
my_api_key = os.environ.get("GOOGLE_API_KEY")

if not my_api_key:
    logger.warning("GOOGLE_API_KEY environment variable not set!")

genai.configure(api_key=my_api_key)

# Initialize Claude service
claude_service = ClaudeService()
if not claude_service.is_configured():
    logger.warning("Claude API service is not configured. Some features will be unavailable.")

# System message to set the agent's context and behavior
SYSTEM_PROMPT = """You are a professional software developer who is very friendly and supportive, helping a user plan their app idea. Your primary goal is to gather information from the user about their app concept and gradually develop a comprehensive masterplan document.

Throughout the conversation, your goal is to understand the user's app idea and its requirements so you can eventually create a masterplan document. be fun and ask friendly questions in list markdown format <li> but no more than 2 or 3 that help you understand the app's purpose, target audience, functionality, and technical requirements.

After you feel you have gathered sufficient information about the app (usually after 5 messages exchanged), proactively offer to create a masterplan. For example, you might say:

"I think I've got a good understanding of your app idea now. Would you like me to generate a comprehensive masterplan document based on our discussion?"

If the user agrees, or when you feel you have enough information to create a helpful masterplan, you should generate a complete masterplan.md file. The masterplan should be in markdown format with the following structure:

# {App Name} - Masterplan

## App Overview and Objectives
[Thorough description of what the app does and its main goals]

## Target Audience
[Description of the primary users and their needs]

## Core Features and Functionality
[Detailed breakdown of the app's main features]

## High-level Technical Stack Recommendations
[Suggested technologies with pros and cons]

## Conceptual Data Model
[Key entities and their relationships]

## User Interface Design Principles
[Guidelines for the app's visual and interaction design]

## Security Considerations
[Important security aspects to address]

## Development Phases or Milestones
[Suggested implementation timeline]

## Potential Challenges and Solutions
[Anticipated difficulties and approaches to solve them]

## Future Expansion Possibilities
[Potential features for future versions]

IMPORTANT: When delivering the masterplan, provide it as a complete markdown document without any introductory text. The masterplan should be structured, comprehensive, and reflect all the information gathered during the conversation.

Remember that creating this masterplan is the ultimate goal of your conversation with the user.
"""

# Agent information
AGENT_PROMPTS = {
    "Requirements": "I'll help you define the core requirements and functionality for your app. Let's discuss what your app needs to do at a high level.",
    "UI/UX": "I'll help you think through UI/UX considerations for your app. Let's discuss user experience flows and interface design principles.",
    "Frontend": "I'll help you plan frontend technical aspects. Let's discuss technologies, frameworks, and implementation approaches for the user interface.",
    "Database": "I'll help you plan data management needs for your app. Let's discuss what kind of data you'll store and how it should be structured.",
    "Backend": "I'll help you design the backend architecture. Let's discuss APIs, services, and server-side implementation."
}

INITIAL_WELCOME = """Hi there! I'm your AI assistant for app development planning. I'll help you understand and plan your app idea through a series of questions. After we discuss your idea thoroughly, I'll generate a comprehensive masterplan.md file as a blueprint for your application.

Let's start with the basics: Could you describe your app idea at a high level? What problem are you trying to solve with this application?"""

def save_drawing_image(base64_image, user_id="user"):
    """
    Save a base64-encoded image to disk
    
    Args:
        base64_image: Base64-encoded image data
        user_id: Identifier for the user (used in the filename)
        
    Returns:
        str: The path to the saved image file
    """
    try:
        # Create uploads directory if it doesn't exist
        uploads_dir = os.path.join(os.getcwd(), 'uploads')
        if not os.path.exists(uploads_dir):
            os.makedirs(uploads_dir)
        
        # Generate a unique filename
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{user_id}_{timestamp}.png"
        filepath = os.path.join(uploads_dir, filename)
        
        # Decode the base64 data
        image_data = base64.b64decode(base64_image)
        
        # Save the image
        with open(filepath, "wb") as f:
            f.write(image_data)
        
        logger.info(f"Drawing image saved to {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Error saving drawing image: {str(e)}")
        return None

@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        user_message = data.get('message', '')
        agent_type = data.get('agent_type', 'Requirements')
        preserve_masterplan = data.get('preserve_masterplan', True)
        drawing_image = data.get('drawing_image')  # Get base64 image if provided
        
        logger.info(f"Received message for {agent_type} agent: {user_message[:50]}...")
        
        # First-time session setup check
        if 'session_id' not in session:
            session['session_id'] = str(uuid.uuid4())
            logger.info(f"Created new session with ID: {session['session_id']}")
        
        # Save the drawing image if provided
        image_path = None
        if drawing_image:
            logger.info("Drawing image received")
            image_path = save_drawing_image(drawing_image, session.get('session_id', 'user'))
            
            # Add information about the image to the message
            if image_path:
                user_message = f"{user_message}\n\n[User has provided sketch drawing of the interface]"
        
        # Initialize conversation history if it doesn't exist
        if 'messages' not in session:
            session['messages'] = []
            session['messages_count'] = 0
            session['first_message'] = True
            logger.info("Initializing message history in session")
        
        # Add the current message to the conversation history
        messages = session.get('messages', [])
        messages.append(user_message)
        session['messages'] = messages
        session['messages_count'] = session.get('messages_count', 0) + 1
        
        # Ensure the session is saved
        session.modified = True
        
        # Track conversation length to determine if it's time to suggest a masterplan
        conversation_length = session.get('messages_count', 0)
        suggest_masterplan = conversation_length >= 10 and not session.get('offered_masterplan', False)
        
        # Build a prompt that includes context
        prompt = ""
        
        # Include the appropriate agent context
        agent_context = AGENT_PROMPTS.get(agent_type, AGENT_PROMPTS["Requirements"])
        prompt += f"{agent_context}\n\n"
        
        # Include system instructions
        prompt += f"{SYSTEM_PROMPT}\n\n"
        
        # Include conversation history (up to 10 messages for context)
        if len(session['messages']) > 1:
            prompt += "Previous messages:\n"
            for i, msg in enumerate(session['messages'][-10:-1]):
                prompt += f"Message {i+1}: {msg}\n"
        
        # Include the current user message
        prompt += f"\nCurrent user message: {user_message}\n\n"
        
        # If we've had several exchanges, suggest creating a masterplan
        if suggest_masterplan:
            prompt += """
            The conversation has progressed significantly. Consider offering to create a masterplan document now
            or if you have enough information, go ahead and create the masterplan document according to the format 
            in your instructions. If you create the masterplan, make sure it is complete and includes all sections.
            """
            session['offered_masterplan'] = True
        
        # Handle first message scenario
        is_first_message = session.get('first_message', True)
        if is_first_message:
            # For the very first message, we might want to send a welcome message
            if user_message.strip():
                # Only mark as not first message if user actually sent content
                session['first_message'] = False
                assistant_message = INITIAL_WELCOME
            else:
                # Empty first message, still send welcome but keep first_message flag
                assistant_message = INITIAL_WELCOME
        else:
            # Generate response based on whether we have an image
            assistant_message = ""
            if image_path and os.path.exists(image_path):
                # Use Claude for image processing
                if claude_service.is_configured():
                    assistant_message = claude_service.process_image(user_message, image_path)
                else:
                    assistant_message = "I'm sorry, but I can't process your sketch as the image analysis service is not configured."
            else:
                # Use Gemini without image
                try:
                    model = genai.GenerativeModel(GEMINI_MODEL)
                    response = model.generate_content(prompt)
                    assistant_message = response.text
                except Exception as e:
                    logger.error(f"Gemini API error: {str(e)}")
                    assistant_message = "I'm sorry, I encountered an issue with generating a response. Please try again in a moment."
        
        # Save any session changes
        session.modified = True
        
        # Check if the response contains a masterplan
        masterplan = extract_masterplan(assistant_message)
        
        # If we find a masterplan, store it in the session
        if masterplan:
            session['masterplan'] = masterplan
            logger.info("Masterplan detected and stored in session, length: " + str(len(masterplan)))
        
        # Extract requirements (simple implementation)
        requirements = extract_requirements(user_message, assistant_message, agent_type)
        
        # Create response object
        response_data = {
            'response': assistant_message,
            'requirements': requirements,
            'isFirstMessage': is_first_message
        }
        
        # If masterplan is present in the response or in session (and we want to preserve it)
        if masterplan:
            response_data['masterplan'] = masterplan
        elif 'masterplan' in session and preserve_masterplan:
            response_data['masterplan'] = session['masterplan']
            logger.info("Including stored masterplan in response")
        
        # Add specialized content based on agent type
        if agent_type == "UI/UX":
            response_data['uiUx'] = extract_specialized_content(assistant_message, "UI/UX")
        elif agent_type in ["Frontend", "Backend", "Database"]:
            response_data['architecture'] = extract_specialized_content(assistant_message, agent_type)
        
        return jsonify(response_data)
    
    except Exception as e:
        logger.error(f"Error in chat endpoint: {str(e)}", exc_info=True)
        return jsonify({
            'response': f"I'm sorry, I encountered an error. Please try again. Technical details: {str(e)}",
            'requirements': {"agent_type": agent_type, "requirement": "Error occurred"},
            'isFirstMessage': False
        }), 500

def extract_masterplan(text):
    """
    Extract masterplan content from the response
    """
    # Check if the text starts with a markdown title and includes typical masterplan sections
    if re.search(r'^#\s+.*?(?:App|Application|MVP)\s+.*?(?:Plan|Blueprint|Masterplan)', text, re.IGNORECASE | re.MULTILINE):
        return text
    
    # Look for markdown content with appropriate headers
    if "# " in text and any(section in text for section in [
        "## App Overview", "## Application Overview", 
        "## Target Audience", "## Core Features", 
        "## Technical Stack", "## Development Phases"
    ]):
        return text
    
    # If the text contains markdown code blocks that might contain the masterplan
    md_pattern = re.compile(r'```(?:md|markdown)?\s([\s\S]*?)```')
    md_matches = md_pattern.findall(text)
    
    for match in md_matches:
        if "# " in match and any(section in match for section in [
            "## App Overview", "## Application Overview", 
            "## Target Audience", "## Core Features", 
            "## Technical Stack", "## Development Phases"
        ]):
            return match.strip()
    
    return None

def extract_specialized_content(text, agent_type):
    """
    Extract specialized content based on agent type
    Simple implementation - would be more sophisticated in a real app
    """
    if agent_type == "UI/UX":
        # Extract UI/UX related content
        return f"## UI/UX Content\n\n{text}"
    elif agent_type in ["Frontend", "Backend", "Database"]:
        # Extract architecture related content
        return f"## {agent_type} Architecture\n\n{text}"
    
    return None

def extract_requirements(user_message, assistant_message, agent_type):
    """
    Simple function to extract potential requirements
    In a production app, this would be more sophisticated
    """
    # This is a placeholder implementation
    # In a real app, you would use NLP to extract requirements
    
    # For now, just return the user message as a potential requirement
    if len(user_message) > 100:
        requirement = user_message[:100] + "..."
    else:
        requirement = user_message
        
    return {
        "agent_type": agent_type,
        "requirement": requirement
    }

@app.route('/reset', methods=['POST'])
def reset():
    try:
        data = request.json or {}
        preserve_masterplan = data.get('preserve_masterplan', True)
        
        # Store masterplan if we want to preserve it
        stored_masterplan = None
        if preserve_masterplan and 'masterplan' in session:
            stored_masterplan = session['masterplan']
            logger.info("Preserving masterplan during reset")
        
        # Store session id before clearing
        session_id = session.get('session_id', str(uuid.uuid4()))
        
        # Clear session
        session.clear()
        
        # Restore session id
        session['session_id'] = session_id
        
        # Initialize a new message history
        session['messages'] = []
        session['messages_count'] = 0
        session['first_message'] = True
        
        # Restore masterplan if needed
        if preserve_masterplan and stored_masterplan:
            session['masterplan'] = stored_masterplan
            logger.info("Restored masterplan after reset")
        
        session.modified = True
        logger.info(f"Session reset (with masterplan preservation: {preserve_masterplan})")
        
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error in reset endpoint: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# The rest of your code (generate-mockups, check-mockup-status, etc.) remains unchanged

@app.route('/generate-mockups', methods=['POST'])
def generate_mockups():
    try:
        data = request.json
        masterplan = data.get('masterplan', '')
        sketch_images = data.get('sketch_images', [])
        
        if not masterplan:
            # Try to get masterplan from session if not provided
            masterplan = session.get('masterplan', '')
            
        if not masterplan:
            return jsonify({
                'success': False,
                'message': 'No masterplan available to generate mockups'
            }), 400
        
        # Start a background job using JobManager
        job_id = JobManager.start_job(
            'mockup',
            claude_service.generate_mockups,
            (masterplan, sketch_images)
        )
        
        # Store the job ID in the session
        session['mockup_job_id'] = job_id
        session.modified = True
        
        # Return immediately with job ID
        return jsonify({
            'success': True,
            'status': 'processing',
            'job_id': job_id,
            'message': 'Mockup generation has started in the background.'
        })
    
    except Exception as e:
        logger.error(f"Error initiating mockup generation: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'Error initiating mockup generation: {str(e)}'
        }), 500

@app.route('/check-mockup-status', methods=['GET'])
def check_mockup_status():
    # Get job ID from request or session
    job_id = request.args.get('job_id') or session.get('mockup_job_id')
    
    if not job_id:
        return jsonify({
            'status': 'error',
            'message': 'No job ID provided or found in session'
        }), 400
    
    # Get the status for this job
    status = JobManager.get_job_status(job_id)
    
    # If the job is completed, store the mockups in the session
    if status.get('status') == 'completed' and status.get('completed', False):
        if 'mockups' in status:
            session['mockups'] = status['mockups']
            session.modified = True
            
            # Return the mockups along with the status
            return jsonify({
                'success': True,
                'status': 'completed',
                'mockups': status['mockups']
            })
    
    # If not completed, just return the status
    return jsonify(status)

@app.route('/generate-architecture', methods=['POST'])
def generate_architecture():
    try:
        data = request.json
        masterplan = data.get('masterplan', '')
        
        if not masterplan:
            # Try to get masterplan from session if not provided
            masterplan = session.get('masterplan', '')
            
        if not masterplan:
            return jsonify({
                'success': False,
                'message': 'No masterplan available to generate architecture diagrams'
            }), 400
        
        # Start a background job
        job_id = JobManager.start_job(
            'arch',
            claude_service.generate_architecture,
            (masterplan,)
        )
        
        # Store the job ID in the session
        session['architecture_job_id'] = job_id
        session.modified = True
        
        # Return immediately with job ID
        return jsonify({
            'success': True,
            'status': 'processing',
            'job_id': job_id,
            'message': 'Architecture diagram generation has started in the background.'
        })
    
    except Exception as e:
        logger.error(f"Error initiating architecture generation: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'Error initiating architecture generation: {str(e)}'
        }), 500

@app.route('/check-architecture-status', methods=['GET'])
def check_architecture_status():
    # Get job ID from request or session
    job_id = request.args.get('job_id') or session.get('architecture_job_id')
    
    if not job_id:
        return jsonify({
            'status': 'error',
            'message': 'No job ID provided or found in session'
        }), 400
    
    # Get the status for this job
    status = JobManager.get_job_status(job_id)
    
    # If the job is completed, store the diagrams in the session
    if status.get('status') == 'completed' and status.get('completed', False):
        if 'diagrams' in status:
            session['architecture_diagrams'] = status['diagrams']
            session.modified = True
            
            # Return the diagrams along with the status
            return jsonify({
                'success': True,
                'status': 'completed',
                'diagrams': status['diagrams']
            })
    
    # If not completed, just return the status
    return jsonify(status)

# Add a health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy', 
        'timestamp': datetime.now().isoformat(),
        'version': '1.0'
    })

# Cleanup job function that runs in a background thread
def cleanup_job():
    while True:
        try:
            JobManager.clean_old_jobs(max_age_hours=24)
            logger.info("Cleaned up old job files")
        except Exception as e:
            logger.error(f"Error cleaning up old job files: {str(e)}")
        
        # Sleep for one hour
        time.sleep(3600)

if __name__ == '__main__':
    logger.info("Starting Flask application with Gemini and Claude integration")
    # Check if the CLAUDE_API_KEY is set
    if os.environ.get("CLAUDE_API_KEY"):
        logger.info("Claude API service is configured and ready")
    else:
        logger.warning("CLAUDE_API_KEY is not set - Claude features will be unavailable")
    
    # Initialize the background cleanup job directly
    cleanup_thread = threading.Thread(target=cleanup_job)
    cleanup_thread.daemon = True
    cleanup_thread.start()
    logger.info("Started background cleanup job")
    
    app.run(debug=True, port=5000)