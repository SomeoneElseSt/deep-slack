import os
import logging
from typing import List
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.cloud import secretmanager
import weave

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Core client wrapper                                                        #
# --------------------------------------------------------------------------- #
class OpenAIResearchClient:
    """Thin wrapper around the Deep-Research model with retries, logging,
    prompt validation and optional Weave tracking."""

    # ------------- lifecycle ------------------------------------------------ #
    def __init__(self, project_id: str = "deep-slack"):
        self.project_id = project_id
        self._client = self._init_openai()
        self._init_weave()

    # ------------- private helpers ----------------------------------------- #
    def _secret(self, name: str) -> str:
        sm = secretmanager.SecretManagerServiceClient()
        path = f"projects/{self.project_id}/secrets/{name}/versions/latest"
        return sm.access_secret_version({"name": path}).payload.data.decode()

    def _init_openai(self) -> OpenAI:
        api_key = self._secret("openai-key")
        logger.info("✅ OpenAI client ready")
        return OpenAI(api_key=api_key)

    def _init_weave(self) -> None:
        try:
            import wandb
            wandb.login(key=self._secret("wandb-api-key"))
            weave.init(project_name="deep-slack-research")
            logger.info("✅ Weave tracking enabled")
        except Exception as e:
            logger.warning(f"Weave disabled: {e}")

    # ------------- public API ---------------------------------------------- #
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    @weave.op()
    def deep_research(self, prompt: str, max_tokens: int = 4000) -> str:
        if not self._is_valid_prompt(prompt):
            raise ValueError("Prompt failed validation")

        logger.info(f"🔎 Researching: {prompt[:80]}…")

        response = self._client.chat.completions.create(
            model="deep-research-preview",          # <--  official Deep-Research alias
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert research assistant. Provide "
                        "comprehensive, well-structured analysis with multiple "
                        "perspectives. Use markdown headings and bullet lists "
                        "suitable for Slack."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=1.0,
            stream=False,
        )
        content = response.choices[0].message.content
        logger.info("✅ Research complete")
        return content

    def format_for_slack(self, md: str) -> str:
        """Convert basic markdown → Slack mrkdwn."""
        md = md.replace("### ", "*").replace("## ", "*").replace("# ", "*")
        # convert **bold** → *bold*
        md = md.replace("**", "*")
        # keep existing *italic* unchanged (don’t mass-replace *)
        return f"🔬 *Deep Research Results* 🔬\n\n{md}"

    # ------------- validation ---------------------------------------------- #
    @staticmethod
    def _is_valid_prompt(prompt: str) -> bool:
        if not prompt or len(prompt.strip()) < 10:
            return False
        banned = {"hack", "illegal", "harmful"}
        return not any(word in prompt.lower() for word in banned)


# --------------------------------------------------------------------------- #
#  Helper factory for Cloud Functions / other callers                         #
# --------------------------------------------------------------------------- #
def create_research_client() -> OpenAIResearchClient:
    project_id = os.getenv("GCP_PROJECT", "deep-slack")
    return OpenAIResearchClient(project_id)
