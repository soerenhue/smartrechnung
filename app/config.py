from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/erechnung.db"
    data_dir: str = "./data"

    seller_name: str = "Meine Firma GmbH"
    seller_street: str = "Musterstrasse 1"
    seller_postcode: str = "10115"
    seller_city: str = "Berlin"
    seller_country: str = "DE"
    seller_tax_id: str = "DE123456789"
    seller_tax_number: str = "12/345/67890"
    seller_email: str = "rechnung@meinefirma.de"
    seller_iban: str = "DE00 0000 0000 0000 0000 00"
    seller_bic: str = "XXXXDEXXX"
    seller_bank: str = "Musterbank"

    require_auth: bool = False
    basic_auth_user: str = "admin"
    basic_auth_pass: str = "change-me"


settings = Settings()
