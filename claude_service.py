import os
import base64
import logging
import anthropic
from datetime import datetime
from jobs import JobManager

logger = logging.getLogger(__name__)

class ClaudeService:
    def __init__(self):
        self.api_key = os.environ.get("CLAUDE_API_KEY")
        if not self.api_key:
            logger.warning("CLAUDE_API_KEY not set in environment variables")
        else:
            self.client = anthropic.Anthropic(api_key=self.api_key)
    
    def is_configured(self):
        """Check if the service is properly configured"""
        return self.api_key is not None
    
    def process_image(self, message, image_path):
        """Process an image with Claude"""
        if not self.is_configured():
            return "Claude API is not configured. Please set the CLAUDE_API_KEY environment variable."
        
        try:
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
            response = self.client.messages.create(
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
            for content in response.content:
                if content.type == "text":
                    response_text += content.text
            
            logger.info("Successfully processed image with Claude")
            return response_text
        
        except Exception as e:
            logger.error(f"Error processing image with Claude: {str(e)}", exc_info=True)
            return f"I encountered an error while analyzing your sketch: {str(e)}"
    
    def generate_mockups(self, masterplan, sketch_images, job_id):
        """Generate UI/UX mockups using Claude in a background job"""
        if not self.is_configured():
            JobManager.save_job_status(job_id, {
                'status': 'error',
                'message': 'Claude API key not configured',
                'completed': True,
                'error_time': datetime.now().isoformat()
            })
            return
        
        try:
            # Update job status to show we're starting the process
            JobManager.save_job_status(job_id, {
                'status': 'processing',
                'progress': 10,
                'message': 'Preparing mockup generation request...',
                'mockups': [],
                'completed': False
            })
            
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
            
            # Update progress
            JobManager.save_job_status(job_id, {
                'status': 'processing',
                'progress': 30,
                'message': f'Calling Claude API to generate mockups with {len(sketch_images) if sketch_images else 0} sketch images...',
                'mockups': [],
                'completed': False
            })
            
            # Add sketch images if provided
            if sketch_images and len(sketch_images) > 0:
                logger.info(f"Processing {len(sketch_images)} sketch images")
                for i, base64_image in enumerate(sketch_images):
                    try:
                        if base64_image:  # Make sure it's not None or empty
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
            
            # Create the message using Anthropic client
            try:
                message = self.client.messages.create(
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
            except Exception as api_error:
                logger.error(f"Claude API error in mockup generation: {str(api_error)}", exc_info=True)
                raise Exception(f"API error: {str(api_error)}")
            
            # Process the response
            mockup_data = []
            
            JobManager.save_job_status(job_id, {
                'status': 'processing',
                'progress': 70,
                'message': 'Processing Claude response...',
                'mockups': [],
                'completed': False
            })
            
            for content_block in message.content:
                if content_block.type == "text":
                    mockup_data.append({
                        'type': 'text',
                        'content': content_block.text
                    })
                elif content_block.type == "image" and getattr(content_block.source, "type", None) == "svg":
                    mockup_data.append({
                        'type': 'svg',
                        'content': content_block.source.data
                    })
            
            # Save the final result
            JobManager.save_job_status(job_id, {
                'status': 'completed',
                'progress': 100,
                'message': 'Mockups generated successfully',
                'mockups': mockup_data,
                'completed': True,
                'completion_time': datetime.now().isoformat()
            })
        
        except Exception as e:
            logger.error(f"Error in background mockup generation: {str(e)}", exc_info=True)
            JobManager.save_job_status(job_id, {
                'status': 'error',
                'message': f'Error generating mockups: {str(e)}',
                'completed': True,
                'error_time': datetime.now().isoformat()
            })
    
    def generate_architecture(self, masterplan, job_id):
        """Generate architecture diagrams using Claude in a background job"""
        if not self.is_configured():
            JobManager.save_job_status(job_id, {
                'status': 'error',
                'message': 'Claude API key not configured',
                'completed': True,
                'error_time': datetime.now().isoformat()
            })
            return
        
        try:
            # Update job status to show we're starting the process
            JobManager.save_job_status(job_id, {
                'status': 'processing',
                'progress': 10,
                'message': 'Preparing architecture diagram request...',
                'diagrams': [],
                'completed': False
            })
            
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
            text_content = f"""Based on this masterplan, please create high level architecture diagram for the application, this should be clear, simple and beautiful:

{masterplan}

Please generate an animated SVG diagram that shows:
1. High-level system architecture
2. Component relationships
3. Data flow between components
4. Any important technical details from the masterplan

Each diagram should be accompanied by explanatory text."""
            
            # Update progress
            JobManager.save_job_status(job_id, {
                'status': 'processing',
                'progress': 30,
                'message': 'Calling Claude API to generate architecture diagrams...',
                'diagrams': [],
                'completed': False
            })
            
            # Call the Claude API
            try:
                message = self.client.messages.create(
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
            except Exception as api_error:
                logger.error(f"Claude API error in architecture generation: {str(api_error)}", exc_info=True)
                raise Exception(f"API error: {str(api_error)}")
            
            # Process the response
            diagram_data = []
            
            JobManager.save_job_status(job_id, {
                'status': 'processing',
                'progress': 70,
                'message': 'Processing Claude response...',
                'diagrams': [],
                'completed': False
            })
            
            for content_block in message.content:
                if content_block.type == "text":
                    diagram_data.append({
                        'type': 'text',
                        'content': content_block.text
                    })
                # Handle SVG content if present
                elif content_block.type == "image" and getattr(content_block.source, "type", None) == "svg":
                    diagram_data.append({
                        'type': 'svg',
                        'content': content_block.source.data
                    })
            
            # Save the final result
            JobManager.save_job_status(job_id, {
                'status': 'completed',
                'progress': 100,
                'message': 'Architecture diagrams generated successfully',
                'diagrams': diagram_data,
                'completed': True,
                'completion_time': datetime.now().isoformat()
            })
        
        except Exception as e:
            logger.error(f"Error in background architecture generation: {str(e)}", exc_info=True)
            JobManager.save_job_status(job_id, {
                'status': 'error',
                'message': f'Error generating architecture diagrams: {str(e)}',
                'completed': True,
                'error_time': datetime.now().isoformat()
            })