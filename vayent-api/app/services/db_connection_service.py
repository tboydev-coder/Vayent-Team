"""Database connection management service."""
import logging
import uuid
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Optional

from app.models import DatabaseConnection
from app.config import get_settings

logger = logging.getLogger(__name__)


class EncryptionService:
    """Service for encrypting/decrypting sensitive database credentials."""

    def __init__(self):
        self.settings = get_settings()
        self.cipher = Fernet(self._get_encryption_key())

    def _get_encryption_key(self) -> bytes:
        """Generate encryption key from dedicated credential secret."""
        from base64 import urlsafe_b64encode
        import hashlib

        source_secret = (
            self.settings.credential_encryption_key or self.settings.secret_key
        )
        key = hashlib.sha256(source_secret.encode()).digest()
        return urlsafe_b64encode(key)

    def encrypt(self, plain_text: str) -> bytes:
        """Encrypt plain text."""
        return self.cipher.encrypt(plain_text.encode())

    def decrypt(self, encrypted_bytes: str | bytes) -> str:
        """Decrypt encrypted bytes or a stored string token."""
        if isinstance(encrypted_bytes, str):
            encrypted_bytes = encrypted_bytes.encode()
        return self.cipher.decrypt(encrypted_bytes).decode()


class DatabaseConnectionService:
    """Service for managing user database connections."""

    def __init__(self):
        self.encryption_service = EncryptionService()

    async def create_connection(
        self,
        user_id: str,
        name: str,
        db_type: str,
        host: str,
        port: int,
        database_name: str,
        username: str,
        password: str,
        db: AsyncSession,
        ssl_mode: str | None = None,
    ) -> DatabaseConnection:
        """Create a new database connection."""
        try:
            # Encrypt credentials
            encrypted_username = self.encryption_service.encrypt(username)
            encrypted_password = self.encryption_service.encrypt(password)

            connection = DatabaseConnection(
                id=str(uuid.uuid4()),
                user_id=user_id,
                name=name,
                db_type=db_type,
                host=host,
                port=port,
                database_name=database_name,
                ssl_mode=ssl_mode,
                encrypted_username=encrypted_username.decode("utf-8"),
                encrypted_password=encrypted_password,
                is_active=True,
            )

            db.add(connection)
            await db.commit()
            await db.refresh(connection)

            logger.info(
                f"Created database connection {connection.id} for user {user_id}")
            return connection

        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to create database connection: {e}")
            raise

    async def get_connection(
        self,
        connection_id: str,
        db: AsyncSession,
    ) -> Optional[DatabaseConnection]:
        """Get a database connection by ID."""
        result = await db.execute(
            select(DatabaseConnection).where(
                DatabaseConnection.id == connection_id
            )
        )
        return result.scalar_one_or_none()

    async def get_user_connections(
        self,
        user_id: str,
        db: AsyncSession,
    ) -> list:
        """Get all connections for a user."""
        result = await db.execute(
            select(DatabaseConnection).where(
                DatabaseConnection.user_id == user_id,
                DatabaseConnection.is_active == True,
            ).order_by(DatabaseConnection.created_at.desc())
        )
        return result.scalars().all()

    async def update_connection(
        self,
        connection_id: str,
        db: AsyncSession,
        **kwargs
    ) -> Optional[DatabaseConnection]:
        """Update a database connection."""
        try:
            connection = await self.get_connection(connection_id, db)

            if not connection:
                return None

            # Update fields
            for key, value in kwargs.items():
                if key == "password" and value:
                    # Encrypt password if provided
                    value = self.encryption_service.encrypt(value)
                    setattr(connection, "encrypted_password", value)
                elif key == "username" and value:
                    setattr(
                        connection,
                        "encrypted_username",
                        self.encryption_service.encrypt(value).decode("utf-8"),
                    )
                elif hasattr(connection, key):
                    setattr(connection, key, value)

            await db.commit()
            await db.refresh(connection)

            logger.info(f"Updated database connection {connection_id}")
            return connection

        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to update database connection: {e}")
            raise

    async def delete_connection(
        self,
        connection_id: str,
        db: AsyncSession,
    ) -> bool:
        """Soft delete a database connection."""
        try:
            connection = await self.get_connection(connection_id, db)

            if not connection:
                return False

            connection.is_active = False
            await db.commit()

            logger.info(f"Deleted database connection {connection_id}")
            return True

        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to delete database connection: {e}")
            raise

    def decrypt_credentials(
        self,
        connection: DatabaseConnection,
    ) -> tuple:
        """
        Decrypt database credentials.

        Returns: (username, password)
        """
        try:
            username = self.encryption_service.decrypt(connection.encrypted_username)
            password = self.encryption_service.decrypt(
                connection.encrypted_password)
            return username, password
        except Exception as e:
            logger.error(f"Failed to decrypt credentials: {e}")
            raise


# Singleton instance
db_connection_service = DatabaseConnectionService()
