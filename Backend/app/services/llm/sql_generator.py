import logging
from typing import Dict, Any

from app.services.llm.groq_service import generate_response
from app.middlewares.sql_sanitizer import sanitize_sql
logger = logging.getLogger(__name__)

DATABASE_SCHEMA = """
Tables:
sales
- id
- date
- revenue
- region
- product

customers
- id
- name
- city
- created_at

orders
- id
- customer_id
- amount
- status
- created_at
"""

SQL_SYSTEM_PROMPT = """
You are an expert PostgreSQL SQL generator.
Your only job:
Generate SAFE read-only SQL queries.

STRICT RULES:
- Only generate SELECT queries
- Never generate DROP, DELETE, UPDATE, ALTER, INSERT
- Never use SELECT *
- Always include LIMIT 100
- Use only tables and columns provided
- Return only raw SQL
- No markdown
- No explaination
- No comments
- Use PostgreSQL syntax

If query is impossible:
Return:
SELECT 'INVALID_QUERY' LIMIT 1
"""

#Build Prompt

def build_sql_prompt(user_query : str)->str:
    return f"""
    Database Schema:
    {DATABASE_SCHEMA}
    User Query:
    {user_query}
    Generate a safe PostgreSQL query.
"""

# clean sql response
# eg. llm may return like this: ```sql
# SELECT id, name FROM customers;
# ```
def clean_sql_response(text : str)-> str:
    """
    Remove markdown/codeblocks/explaination
    """