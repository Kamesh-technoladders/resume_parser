import os
from supabase import create_client, Client
from google.generativeai import GenerativeModel, configure
from dotenv import load_dotenv
from redis import Redis
from rq import Queue

# Load environment variables from .env file (if present)
load_dotenv()

# Load environment variables
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET")
SUPABASE_RESUME_PATH_PREFIX = os.getenv("SUPABASE_RESUME_PATH_PREFIX")
SUPABASE_REPORT_PATH_PREFIX = os.getenv("SUPABASE_REPORT_PATH_PREFIX")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", 5005))

# Validate environment variables
if not all([SUPABASE_URL, SUPABASE_KEY, SUPABASE_STORAGE_BUCKET, SUPABASE_RESUME_PATH_PREFIX, SUPABASE_REPORT_PATH_PREFIX, GEMINI_API_KEY]):
    raise ValueError("Missing required environment variables. Ensure SUPABASE_URL, SUPABASE_KEY, SUPABASE_STORAGE_BUCKET, SUPABASE_RESUME_PATH_PREFIX, SUPABASE_REPORT_PATH_PREFIX, and GEMINI_API_KEY are set.")

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Configure Gemini API
configure(api_key=GEMINI_API_KEY)
gemini_model = GenerativeModel("gemini-1.5-pro")

# Initialize Redis and RQ queue
redis_conn = Redis(host='redis', port=6379)
queue = Queue(connection=redis_conn)