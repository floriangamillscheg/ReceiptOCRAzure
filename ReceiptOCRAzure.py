import io
import random

from PIL import UnidentifiedImageError
from PIL import Image
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult, DocumentAnalysisFeature, DocumentField
import os
import json
import re
from datetime import datetime
from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import logging
import sys


def safe_get(receipt: AnalyzeResult, field_name: str):
    return receipt.fields.get(field_name) if field_name in receipt.fields else None


def _format_tax_details(receipt: AnalyzeResult):
    tax_details = safe_get(receipt, "TaxDetails")
    tax_info = {}
    tax_list = []
    if tax_details:
        for idx, tax_detail in enumerate(tax_details.value_array):
            tax_obj = tax_detail.value_object
            rate_field = tax_obj.get("Rate")
            rate = getattr(rate_field, "value_number", None) if rate_field else None

            net_field = tax_obj.get("NetAmount")
            net_currency = getattr(net_field, "value_currency", None) if net_field else None
            net_amount = getattr(net_currency, "amount", None) if net_currency else None

            tax_field = tax_obj.get("Amount")
            tax_currency = getattr(tax_field, "value_currency", None) if tax_field else None
            tax_amount = getattr(tax_currency, "amount", None) if tax_currency else None

            logger.debug(f"Tax Rate: {rate}%, Net Amount: {net_amount}, Tax Amount: {tax_amount}")
            tax_list.append({
                "Rate": rate * 100 if rate else None,
                "Netto": net_amount,
                "TaxAmount": tax_amount,
                "Brutto": round(net_amount + tax_amount, 2) if net_amount and tax_amount else None,
                "Currency": getattr(tax_currency, "currency_code", None) if tax_currency else None
            })
        return tax_list
    return "No tax information found"


def _format_type(receipt_type: DocumentField):
    if receipt_type:
        type_value = getattr(receipt_type, "value_string", None)
        logger.debug("Receipt type value:", type_value)
        if type_value and type_value.strip():
            type_map = {
                "Transportation.Parking": "Parken",
                "Transportation.Taxi": "Taxi",
                "Supplies": "Einkauf/Material",
                "Hotel": "Hotel",
                "Meal": "Essen/Restaurant",
            }
            return type_map.get(type_value, "Unknown type")


def _format_UID_number(UID_number: DocumentField):
    if UID_number and UID_number.confidence and UID_number.confidence > 0.7:
        UID_string = getattr(UID_number, "value_string", None)
        if UID_string:
            vat_pattern = r"[A-Z]{2}[A-Z0-9]{8,12}"
            if re.match(vat_pattern, UID_string):
                return UID_string
            return "UID format invalid"
    return "UID not found"


def get_random_image(path="images"):
    # List all files (filtering out hidden files and directories)
    files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f)) and not f.startswith(".")]

    if not files:
        raise FileNotFoundError("No files found in the directory.")

    # Pick a random file
    random_file = random.choice(files)

    return os.path.join(path, random_file)


def _format_price(price_dict):
    if price_dict is None:
        return "N/A"
    return "".join([f"{p}" for p in price_dict.values()])


app = FastAPI()
load_dotenv()
endpoint = os.getenv("DOCUMENTINTELLIGENCE_ENDPOINT")
key = os.getenv("DOCUMENTINTELLIGENCE_API_KEY")
logger = logging.getLogger("uvicorn.error")
logger.setLevel(logging.DEBUG)
if not endpoint or not key:
    raise RuntimeError("Azure Document Intelligence credentials not found in environment variables.")
document_intelligence_client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))


@app.post("/process_image")
async def process_image(image_file: UploadFile):
    if not (image_file.content_type.startswith('image/') or image_file.content_type.startswith('application/pdf')):
        raise HTTPException(status_code=400, detail="Invalid file type. Please upload an image.")
    if image_file.content_type.startswith('image/'):
        try:
            contents = await image_file.read()
            logger.debug('here 1')
            image_bytes = io.BytesIO(contents)

            img = Image.open(image_bytes)
            img.load()  # safer than .verify()
            logger.debug('Image loaded successfully')

        except Exception as e:
            logger.error(f"Image load failed: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid image: {str(e)}")
    else:
        try:
            contents = await image_file.read()
            logger.debug('here 2')
            # For PDF files, we assume the content is already in bytes
            if not contents:
                raise HTTPException(status_code=400, detail="Empty PDF file.")
        except Exception as e:
            logger.error(f"PDF load failed: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid PDF: {str(e)}")

    poller = document_intelligence_client.begin_analyze_document("prebuilt-receipt", body=io.BytesIO(contents),
                                                                 locale="de",
                                                                 features=[DocumentAnalysisFeature.QUERY_FIELDS],
                                                                 query_fields=["UID"], )
    receipts: AnalyzeResult = poller.result()

    if receipts.documents:
        for idx, receipt in enumerate(receipts.documents):
            logger.debug(f"--------Analysis of receipt #{idx + 1}--------")
            logger.debug(f"Receipt type: {receipt.doc_type if receipt.doc_type else 'N/A'}")
            if receipt.fields:
                subtotal = receipt.fields.get("Subtotal")
                receipt_type = receipt.fields.get("ReceiptType")
                date_obj = receipt.fields.get("TransactionDate")
                date_value = getattr(date_obj, "value_date", None) if date_obj else None
                tax_details = _format_tax_details(receipt)
                tip = receipt.fields.get("Tip")
                total_obj = receipt.fields.get("Total")
                total_currency = getattr(total_obj, "value_currency", None) if total_obj else None
                total_amount = getattr(total_currency, "amount", None)  # Ensure total has value_currency
                UID_number = receipt.fields.get("UID")
                output = {
                    "filename": image_file.filename if image_file else None,  # TODO
                    "Date": date_value.strftime("%d-%m-%Y") if date_value else None,
                    "Type": _format_type(receipt_type),
                    "BruttoTotal": total_amount,
                    "UID-number": _format_UID_number(UID_number),  # Placeholder — replace with regex if needed
                    "Tip": safe_get(receipt, "Tip").value_currency.amount if safe_get(receipt, "Tip") else None,
                    "Taxes": tax_details
                }
                json_str = json.dumps(output, indent=2, ensure_ascii=False)
                return JSONResponse(content=output)
