import gspread
from google.oauth2.service_account import Credentials

# Path ke file JSON yang kamu download
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)

client = gspread.authorize(creds)

# Ganti dengan nama sheet atau ID sheet-mu
sheet = client.open("Expenses").sheet1

# Test tambah row
sheet.append_row(["2026-03-12", "Test", "FnB", "needs", "", "Cash", 10000])

print("Sukses! Cek sheet-mu, ada row baru.")