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
from logging.handlers import RotatingFileHandler
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

            logger.info(f"Tax Rate: {rate}%, Net Amount: {net_amount}, Tax Amount: {tax_amount}")
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
        # logger.debug("Receipt type value:", type_value)
        # if type_value and type_value.strip():
        #     type_map = {
        #         "Transportation.Parking": "Parken",
        #         "Transportation.Taxi": "Taxi",
        #         "Supplies": "Einkauf/Material",
        #         "Hotel": "Hotel",
        #         "Meal": "Essen/Restaurant",
        #     }
        return type_value  # type_map.get(type_value, type_value)


def _format_UID_number(UID_number: DocumentField):
    if UID_number and UID_number.confidence and UID_number.confidence > 0.7:
        UID_string = getattr(UID_number, "value_string", None)
        if UID_string:
            vat_pattern = r"[A-Z]{2}[A-Z0-9]{8,12}"
            if re.match(vat_pattern, UID_string):
                return UID_string
            return "UID format invalid"
    return "UID not found"

def compute_average_confidence(receipt) -> float:
    if not receipt.fields:
        return 0.0

    confidences = [
        field.confidence
        for field in receipt.fields.values()
        if field.confidence is not None
    ]

    if not confidences:
        return 0.0

    return round(sum(confidences) / len(confidences), 4)


def _format_price(price_dict):
    if price_dict is None:
        return "N/A"
    return "".join([f"{p}" for p in price_dict.values()])


os.makedirs("logs", exist_ok=True)
app = FastAPI()
load_dotenv()
endpoint = os.getenv("DOCUMENTINTELLIGENCE_ENDPOINT")
key = os.getenv("DOCUMENTINTELLIGENCE_API_KEY")

file_handler = RotatingFileHandler(
    filename="logs/ocr_api.log",
    mode="a",
    maxBytes=1024*1024,   # 5MB
    backupCount=3,
    encoding="utf-8"
)
console_handler = logging.StreamHandler()
# Formatter
formatter = logging.Formatter(
    '%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)
# Root logger
logger = logging.getLogger("ocr_logger")
logger.setLevel(logging.DEBUG)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

if not endpoint or not key:
    raise RuntimeError("Azure Document Intelligence credentials not found in environment variables.")
document_intelligence_client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))


@app.post("/process_image")
async def process_image(image_file: UploadFile):
    if not (image_file.content_type.startswith('image/') or image_file.content_type.startswith('application/pdf')):
        logger.error(f"Invalid file type")
        raise HTTPException(status_code=400, detail="Invalid file type. Please upload an image.")
    if image_file.content_type.startswith('image/'):
        try:
            contents = await image_file.read()
            image_bytes = io.BytesIO(contents)

            img = Image.open(image_bytes)
            img.load()  # safer than .verify()
            logger.info(f'Image {image_file.filename} loaded successfully')

        except Exception as e:
            logger.error(f"Image load failed: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid image: {str(e)}")
    else:
        try:
            contents = await image_file.read()
            if not contents:
                raise HTTPException(status_code=400, detail="Empty PDF file.")
        except Exception as e:
            logger.error(f"PDF load failed: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid PDF: {str(e)}")

    poller = document_intelligence_client.begin_analyze_document("prebuilt-receipt", body=io.BytesIO(contents),
                                                                 locale="de")
    receipts: AnalyzeResult = poller.result()

    if receipts.documents:
        for idx, receipt in enumerate(receipts.documents):
            logger.debug(f"Number of receipts: {len(receipts.documents)}")
            logger.info(f"--------Analysis of receipt #{image_file.filename}--------")
            if receipt.fields:
                validation_errors = []
                receipt_type = receipt.fields.get("ReceiptType")
                country_obj = receipt.fields.get("CountryRegion")
                date_obj = receipt.fields.get("TransactionDate")
                time_obj = receipt.fields.get("TransactionTime")
                date_value = getattr(date_obj, "value_date", None) if date_obj else None
                date_confidence = date_obj.confidence if date_obj else 0.0
                time_value = getattr(time_obj, "value_time", None) if time_obj else None
                country_value = getattr(country_obj, "value_country_region", None) if country_obj else None
                tax_details = _format_tax_details(receipt)
                total_obj = receipt.fields.get("Total")
                total_currency = getattr(total_obj, "value_currency", None) if total_obj else None
                total_amount = getattr(total_currency, "amount", None)  # Ensure total has value_currency
                total_confidence = total_obj.confidence if total_obj else None
                average_confidence = compute_average_confidence(receipt)
                output = {
                    "Filename": image_file.filename if image_file else None,  # TODO
                    "Confidence": average_confidence,
                    "Country": country_value,
                    "Date": date_value.strftime("%d-%m-%Y") if date_value else None,
                    "Time": time_value.strftime("%H:%M:%S") if date_value else None,
                    "Type": _format_type(receipt_type),
                    "BruttoTotal": total_amount,
                    "Tip": safe_get(receipt, "Tip").value_currency.amount if safe_get(receipt, "Tip") else None,
                    "Taxes": tax_details
                }
                if average_confidence < 0.5:
                    validation_errors = [f"Average confidence is too low ({average_confidence:.2f} < 0.5)."]
                elif total_amount is None:
                    validation_errors.append("BruttoTotal is missing.")
                elif total_confidence < 0.5:
                    validation_errors.append(f"BruttoTotal confidence is too low ({total_confidence:.2f} < 0.5).")
                # Check Date
                if date_value is None:
                    validation_errors.append("Date is missing.")
                elif date_confidence < 0.5:
                    validation_errors.append(f"Date confidence is too low ({date_confidence:.2f} < 0.5).")
                if validation_errors:
                    logger.error(f"Validation errors for receipt {image_file.filename}: {validation_errors}")
                    raise HTTPException(status_code=400, detail=f"Validation errors: {', '.join(validation_errors)}")
                logger.info(f"Receipt processed: {output}")
                return JSONResponse(content=output)
