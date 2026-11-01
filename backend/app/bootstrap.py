from getpass import getpass

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Organization, Role, User
from app.security import hash_password


def main() -> None:
    organization_name = input("Organization name: ").strip()
    email_input = input("Administrator email: ").strip().lower()
    try:
        email = str(TypeAdapter(EmailStr).validate_python(email_input))
    except ValidationError as exc:
        raise SystemExit("Enter a valid administrator email address.") from exc
    password = getpass("Administrator password (minimum 14 characters): ")
    confirmation = getpass("Confirm password: ")

    if not organization_name:
        raise SystemExit("Organization name is required.")
    if len(password) < 14:
        raise SystemExit("Password must contain at least 14 characters.")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")

    with SessionLocal.begin() as db:
        if db.scalar(select(func.count()).select_from(User)):
            raise SystemExit("Bootstrap is only permitted before the first user exists.")
        if db.scalar(select(func.count()).select_from(Organization)):
            raise SystemExit("Bootstrap is only permitted before the first organization exists.")
        organization = Organization(name=organization_name)
        db.add(organization)
        db.flush()
        db.add(
            User(
                organization_id=organization.id,
                email=email,
                password_hash=hash_password(password),
                role=Role.ADMINISTRATOR.value,
            )
        )

    print("Initial administrator created. Store the password in an approved password manager.")


if __name__ == "__main__":
    main()
