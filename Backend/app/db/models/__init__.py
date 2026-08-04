"""Import every mapped model here so SQLAlchemy's declarative registry knows
about all of them up front.

User's relationships reference sibling models by string ("FileUpload",
"Payment", "QueryLogs"). SQLAlchemy only resolves those strings against
classes that have actually been imported somewhere by the time mappers are
configured - which happens automatically the first time any query touches
User. Nothing in the app's normal import path (routes -> services -> User)
ever imports file_upload.py / payment.py / query_logs.py, so without this
file the very first signup/login call crashes with:
    InvalidRequestError: failed to locate a name ('FileUpload')
"""

from app.db.models.user import User
from app.db.models.file_upload import FileUpload
from app.db.models.payment import Payment
from app.db.models.query_logs import QueryLogs

__all__ = ["User", "FileUpload", "Payment", "QueryLogs"]
