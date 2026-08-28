import json
import os
from datetime import datetime

from dotenv import load_dotenv
from google import genai


def main():
    load_dotenv()
    client_ai = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

    PARSING_PROMPT = """
    Kamu adalah expense parser yang akurat untuk aplikasi tracking pengeluaran pribadi.
    Parse kalimat input pengguna berikut dalam bahasa Indonesia, Inggris, atau campur (slang OK):

    "{user_input}"

    Aturan ketat:
    - Output HANYA JSON valid, tanpa teks tambahan, tanpa markdown, tanpa penjelasan.
    - Field wajib: date, description, category, type, tag, source, amount
    - date: format "YYYY-MM-DD". Gunakan hari ini "{today}" jika tidak disebutkan. Gunakan local timezone Jakarta default jika tidak ada info waktu.
    - description: ringkasan singkat dari input (max 64 char) dalam English, jangan translate nama produk dan tempat.
    - category: pilih salah satu atau infer: Groceries, Supplies, Transport, Utilities, Entertainment, Health, FnB, Shopping, Bill, Donation, Social, Other. Prioritaskan Groceries untuk makanan rumah, Supplies untuk perlengkapan rumah non-makanan.
    - type: "needs" jika essential (makan sehari-hari, transport kerja, tagihan, kebutuhan rumah), "wants" jika discretionary (makan luar, hiburan, belanja impulsif, luxury). Prioritaskan needs/wants dari input saat parse jika tersedia.
    - tag: highlighted tag jika ada (contoh: urgent, luxury, refund, friend-split), atau "" jika tidak ada.
    - source: metode pembayaran: Cash, BCA, BNI, CIMB, GoPay, Credit Card, dll. Infer jika tidak disebut.
    - amount: angka integer tanpa Rp, koma, atau titik (contoh: 50000 untuk Rp50.000). Konversi mata uang asing ke IDR berdasarkan kurs saat ini jika perlu.

    Jika input tidak jelas atau bukan expense → return {{"error": "invalid_input"}}

    Contoh output:
    {{"date":"2026-03-12","category":"Groceries","type":"needs","tag":"","source":"Cash","amount":45000,"description":"Beli beras dan sayur"}}
    """

    user_input = "sore ini makan bakso 2 pcs 24000 cash"
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        full_prompt = PARSING_PROMPT.format(user_input=user_input, today=today)
        print("Prompt OK")
    except Exception as e:
        print(f"PROMPT FORMAT ERROR: {type(e).__name__}: {e}")
        raise SystemExit(1)

    try:
        response = client_ai.models.generate_content(model=model, contents=full_prompt)
        print(f"Raw response: [{response.text}]")
    except Exception as e:
        print(f"GEMINI ERROR: {type(e).__name__}: {e}")
        raise SystemExit(1)

    json_str = response.text.strip()
    if json_str.startswith("```"):
        json_str = json_str.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    print(f"Cleaned JSON: [{json_str}]")

    try:
        parsed = json.loads(json_str)
        print(f"Parsed OK: {json.dumps(parsed, indent=2)}")
    except json.JSONDecodeError as e:
        print(f"JSON DECODE ERROR: {e}")

    try:
        row = [
            parsed["date"],
            parsed["description"],
            parsed["category"],
            parsed["type"],
            parsed["tag"],
            parsed["source"],
            parsed["amount"],
        ]
        print(f"Row: {row}")
    except KeyError as e:
        print(f"MISSING KEY: {e}")
        print(f"Available keys: {list(parsed.keys())}")


if __name__ == "__main__":
    main()
