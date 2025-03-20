from flask import Flask, request, jsonify, session
from flask_cors import CORS
import google.generativeai as genai
import os
from dotenv import load_dotenv
import re
import logging
import requests
import json
from flask import send_file
import base64
from io import BytesIO
from PIL import Image
import anthropic
from anthropic.types import ContentBlock, MessageParam
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables first, before any other code
load_dotenv()
app = Flask(__name__)

# Configure CORS to allow requests from your frontend
CORS(app, supports_credentials=True, origins=['http://localhost:3000', 'https://agnets-requirements-pib3.vercel.app/'])

app.secret_key = os.environ.get("SECRET_KEY", "123454")

# Using Gemini 1.5 Pro for better context handling
GEMINI_MODEL = "models/gemini-1.5-pro" 
my_api_key = os.environ.get("GOOGLE_API_KEY")

if not my_api_key:
    logger.warning("GOOGLE_API_KEY environment variable not set!")

genai.configure(api_key=my_api_key)

# System message to set the agent's context and behavior
SYSTEM_PROMPT = """You are a professional software developer who is very friendly and supportive, helping a user plan their app idea. Your primary goal is to gather information from the user about their app concept and gradually develop a comprehensive masterplan document.

Throughout the conversation, your goal is to understand the user's app idea and its requirements so you can eventually create a masterplan document. be fun and ask friendly questions in list markdown format <li> but no more than 2 or 3 that help you understand the app's purpose, target audience, functionality, and technical requirements.

After you feel you have gathered sufficient information about the app (usually after 5-6 messages exchanged), proactively offer to create a masterplan. For example, you might say:

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
        logger.error(f"Error saving drawing imag: {str(e)}")
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
        
        # Save the drawing image if provided
        image_path = None
        if drawing_image:
            logger.info("Drawing image received")
            image_path = save_drawing_image(drawing_image)
            
            # Add information about the image to the message
            if image_path:
                user_message = f"{user_message}\n\n[User has provided a sketch drawing of the interface]"
        
        # Initialize conversation history if it doesn't exist
        if 'messages' not in session:
            session['messages'] = []
            session['messages_count'] = 0
            logger.info("Creating new session")
        
        # Add the current message to the conversation history
        session['messages'].append(user_message)
        session['messages_count'] = session.get('messages_count', 0) + 1
        
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
        
        # Initialize Gemini model
        model = genai.GenerativeModel(GEMINI_MODEL)
        
        # Generate response - if we have an image, use it with Claude instead
        if image_path and os.path.exists(image_path):
            # Use Claude for image processing
            assistant_message = process_with_claude(user_message, image_path)
        else:
            # Use Gemini without image
            response = model.generate_content(prompt)
            assistant_message = response.text
        
        # Store in session
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
            'isFirstMessage': session['messages_count'] <= 2
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

def process_with_claude(message, image_path):
    try:
        # Get Claude API key from environment
        claude_api_key = os.environ.get("CLAUDE_API_KEY")
        if not claude_api_key:
            logger.error("CLAUDE_API_KEY not set in environment variables")
            return "I'm sorry, I can't process images without the Claude API being properly configured."
        
        # Initialize the Anthropic client
        client = anthropic.Anthropic(api_key=claude_api_key)
        
        # Read the image file and encode it as base64
        with open(image_path, "rb") as image_file:
            image_data = base64.b64encode(image_file.read()).decode('utf-8')
        
        # Prepare system prompt for sketch analysis
        system_prompt = """You are a helpful AI assistant that can analyze UI/UX sketches and drawings.
        When a user provides a sketch, analyze it in detail and describe:
        1. The overall layout and structure
        2. The key UI elements you can identify
        3. The apparent workflow or user journey
        4. Any design patterns you notice
        
        Provide constructive feedback and suggestions for improving the design, keeping in mind 
        standard UI/UX best practices. Be specific and helpful."""
        
        # Call Claude API with the image
        message = client.messages.create(
            model="claude-3-7-sonnet-20250219",
            max_tokens=4000,
            temperature=0.7,
            system=system_prompt,
            messages=[
                {
                    "role": "user", 
                    "content": [
                        {
                            "type": "text", 
                            "text": message
                        },
                        {
                            "type": "image", 
                            "source": {
                                "type": "base64", 
                                "media_type": "image/png", 
                                "data": image_data
                            }
                        }
                    ]
                }
            ]
        )
        
        # Extract the text response
        response_text = ""
        for content in message.content:
            if content.type == "text":
                response_text += content.text
        
        logger.info("Successfully processed image with Claude")
        return response_text
    
    except Exception as e:
        logger.error(f"Error processing image with Claude: {str(e)}", exc_info=True)
        return f"I encountered an error while analyzing your sketch: {str(e)}"

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
        
        # Clear session
        session.clear()
        
        # Restore masterplan if needed
        if preserve_masterplan and stored_masterplan:
            session['masterplan'] = stored_masterplan
            logger.info("Restored masterplan after reset")
        
        session.modified = True
        logger.info("Session reset (with masterplan preservation)" if preserve_masterplan else "Session reset (complete)")
        
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error in reset endpoint: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

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
        
        # Get Claude API key from environment
        claude_api_key = os.environ.get("CLAUDE_API_KEY")
        if not claude_api_key:
            logger.error("CLAUDE_API_KEY not set in environment variables")
            return jsonify({
                'success': False,
                'message': 'Claude API key not configured'
            }), 500
        
        # Initialize the Anthropic client
        client = anthropic.Anthropic(api_key=claude_api_key)
        
        # Prepare sketch descriptions and images
        image_content = []
        
        # Add masterplan as text
        image_content.append({
            "type": "text",
            "text": f"""I need you to create UI/UX mockups for an application based on this masterplan:
            
            {masterplan}
            
            Please create detailed SVG mockups for the main screens. For each mockup, first describe the screen's purpose,
            then provide the visual representation using SVG."""
        })
        
        # Add sketch images if provided
        for i, base64_image in enumerate(sketch_images):
            try:
                image_content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64_image
                    }
                })
                
                # Add a description after each image
                image_content.append({
                    "type": "text",
                    "text": f"This is user sketch #{i+1}. Please consider this sketch when designing the mockups."
                })
            except Exception as e:
                logger.error(f"Error adding sketch image {i} to Claude request: {str(e)}")
        
        logger.info(f"Calling Claude API to generate mockups with {len(sketch_images)} sketch images")
        
        # Create the message using Anthropic client
        message = client.messages.create(
            model="claude-3-7-sonnet-20250219",
            max_tokens=5000,
            temperature=0.7,
            system="You are a professional UI/UX designer. Create detailed UI/UX mockups as SVG, be sure to gather and use the correct key aspects of the masterplan, the mockups must be intuitive, and user friendly, be sure that SVG replaces all escaped newlines (\\n) with actual line breaks.",
            messages=[
                {
                    "role": "user", 
                    "content": image_content
                }
            ]
        )
        
        # Process the response
        mockup_data = []
        
        for content_block in message.content:
            if content_block.type == "text":
                mockup_data.append({
                    'type': 'text',
                    'content': content_block.text
                })
            elif content_block.type == "image" and getattr(content_block, "source", {}).get("type") == "svg":
                mockup_data.append({
                    'type': 'svg',
                    'content': content_block.source.data
                })
        
        # Store mockups in session
        session['mockups'] = mockup_data
        session.modified = True
        
        return jsonify({
            'success': True,
            'mockups': mockup_data
        })
    except Exception as e:
        logger.error(f"Error generating mockups: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'Error generating mockups: {str(e)}'
        }), 500

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
        
        # Get Claude API key from environment
        claude_api_key = os.environ.get("CLAUDE_API_KEY")
        if not claude_api_key:
            logger.error("CLAUDE_API_KEY not set in environment variables")
            return jsonify({
                'success': False,
                'message': 'Claude API key not configured'
            }), 500
        
        # Initialize the Anthropic client
        client = anthropic.Anthropic(api_key=claude_api_key)
        
        # Prepare system prompt
        system_prompt = """You are a professional software architect specializing in creating clear, informative architecture 
        diagrams. Create SVG diagrams that illustrate the system architecture based on the masterplan provided.
        
        For each diagram:
        1. First describe the architecture component in detail
        2. Then provide an SVG diagram representation
        3. Make sure SVG code is clean and renders properly (replace escaped newlines with actual breaks)
        4. Include animations where appropriate to illustrate data flow
        5. Use appropriate colors to differentiate components (frontend, backend, database, etc.)
        
        Create multiple diagrams:
        1. A high-level system architecture overview
        2. A more detailed component diagram
        3. A data flow diagram
        
        Each SVG should be clear, professional, and help visualize the application structure."""
        
        # Create the message content
        text_content = f"""Based on this masterplan, please create high level architecture diagram for the application, this should be clear, simple and beatiful:

{masterplan}

Please generate an animated SVG diagram that show:
1. High-level system architecture
2. Component relationships
3. Data flow between components
4. Any important technical details from the masterplan

Each diagram should be accompanied by explanatory text."""
        
        # Call the Claude API
        logger.info("Calling Claude API to generate architecture diagrams")
        message = client.messages.create(
            model="claude-3-7-sonnet-20250219",
            max_tokens=5000,
            temperature=0.7,
            system=system_prompt,
            messages=[
                {
                    "role": "user", 
                    "content": [
                        {
                            "type": "text", 
                            "text": text_content
                        }
                    ]
                }
            ]
        )
        
        # Process the response
        diagram_data = []
        
        for content_block in message.content:
            if content_block.type == "text":
                diagram_data.append({
                    'type': 'text',
                    'content': content_block.text
                })
            # Handle SVG content if present
            elif content_block.type == "image" and getattr(content_block, "source", {}).get("type") == "svg":
                diagram_data.append({
                    'type': 'svg',
                    'content': content_block.source.data
                })
        
        # Store diagrams in session
        session['architecture_diagrams'] = diagram_data
        session.modified = True
        
        return jsonify({
            'success': True,
            'diagrams': diagram_data
        })
    except Exception as e:
        logger.error(f"Error generating architecture diagrams: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'Error generating architecture diagrams: {str(e)}'
        }), 500

if __name__ == '__main__':
    logger.info("Starting Flask application with Gemini integration - Goal-oriented version")
    app.run(debug=True, port=5000)