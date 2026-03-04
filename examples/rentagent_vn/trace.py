import os

from dotenv import load_dotenv
from langfuse import Langfuse, get_client, observe
from langfuse.langchain import CallbackHandler

load_dotenv()

LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST")

# Initialize Langfuse client with constructor arguments
Langfuse(
    public_key=LANGFUSE_PUBLIC_KEY,
    secret_key=LANGFUSE_SECRET_KEY,
    host=LANGFUSE_HOST,
)

# Get the configured client instance
langfuse = get_client()

# Initialize the Langfuse handler
langfuse_handler = CallbackHandler()

# Decorator
observe = observe
