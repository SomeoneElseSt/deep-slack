# openai_client.py
import os
import logging
from typing import Optional
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.cloud import secretmanager
import weave

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OpenAIResearchClient:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.client = None
        self._initialize_client()
        self._initialize_weave()
    
    def _initialize_weave(self):
        """Initialize Weave tracking with W&B authentication"""
        try:
            # Weave will use WANDB_API_KEY from environment
            # Or prompt for login if not set
            weave.init(project_name="deep-slack-research")
            logger.info("Weave tracking initialized successfully")
            
        except Exception as e:
            logger.warning(f"Weave initialization failed: {e}")
            # Continue without Weave if it fails
    
    def _get_secret(self, secret_name: str) -> str:
        """Retrieve secret from Google Secret Manager"""
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{self.project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    
    def _initialize_client(self):
        """Initialize OpenAI client with API key from Secret Manager"""
        try:
            # Updated to use your new key name
            api_key = self._get_secret("openai-key")  # This should match your Secret Manager key name
            self.client = OpenAI(api_key=api_key)
            logger.info("OpenAI client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
            raise
    
    @weave.op()
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((Exception,))
    )
    def deep_research(self, prompt: str, max_tokens: int = 4000) -> str:
        """
        Perform deep research using OpenAI's Deep Research API
        
        Args:
            prompt: Research prompt from user
            max_tokens: Maximum tokens for response
            
        Returns:
            Research results as formatted string
        """
        try:
            logger.info(f"Starting deep research for prompt: {prompt[:100]}...")
            
            # Enhanced system prompt for deep research
            system_prompt = """You are an expert research assistant. Your task is to provide comprehensive, well-structured research on the given topic. 

            Guidelines:
            - Provide in-depth analysis with multiple perspectives
            - Include relevant facts, statistics, and examples
            - Structure your response with clear headings and bullet points
            - Be objective and cite reasoning for conclusions
            - Format for easy reading in Slack (use markdown)
            - Aim for thoroughness while remaining concise
            """
            
            # Correct Deep Research API call based on OpenAI specification
            response = self.client.chat.completions.create(
                model="o1-preview",  # Deep Research model
                messages=[
                    {"role": "user", "content": f"{system_prompt}\n\nResearch topic: {prompt}"}
                ],
                max_completion_tokens=max_tokens,  # Note: max_completion_tokens instead of max_tokens for o1 models
                temperature=1.0,  # Fixed temperature for o1 models
                stream=False
            )
            
            result = response.choices[0].message.content
            logger.info("Deep research completed successfully")
            return result
            
        except Exception as e:
            logger.error(f"Deep research failed: {e}")
            raise
    
    def format_for_slack(self, content: str) -> str:
        """
        Format research content for Slack display
        
        Args:
            content: Raw research content
            
        Returns:
            Slack-formatted content
        """
        # Convert markdown to Slack formatting
        formatted = content
        
        # Convert markdown headers to Slack bold
        formatted = formatted.replace("### ", "*")
        formatted = formatted.replace("## ", "*")
        formatted = formatted.replace("# ", "*")
        
        # Convert markdown bold to Slack bold
        formatted = formatted.replace("**", "*")
        
        # Convert markdown italic to Slack italic
        formatted = formatted.replace("*", "_")
        
        # Add separator for readability
        formatted = f"🔬 *Deep Research Results* 🔬\n\n{formatted}"
        
        return formatted
    
    def validate_prompt(self, prompt: str) -> bool:
        """
        Validate research prompt for safety and quality
         
        Args:
            prompt: User-provided research prompt
            
        Returns:
            True if prompt is valid, False otherwise
        """
        if not prompt or len(prompt.strip()) < 10:
            return False
            
        # Add content policy checks here if needed
        forbidden_keywords = ["hack", "illegal", "harmful"]
        if any(keyword in prompt.lower() for keyword in forbidden_keywords):
            return False
            
        return True

# Helper function for Firebase Functions
def create_research_client() -> OpenAIResearchClient:
    """Create OpenAI research client with current project ID"""
    project_id = os.getenv("GCP_PROJECT", "deep-slack")
    return OpenAIResearchClient(project_id)
