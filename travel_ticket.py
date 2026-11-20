"""Local, optional ticket OCR. Only confirmed structured fields go into a run."""
import os
import re
import shutil
import subprocess


def suggested_fields(text):
    fields={}
    d=re.search(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})",text)
    if d:
        from datetime import date
        try:
            fields["date"]=date(*map(int,d.groups())).isoformat()
        except ValueError:
            pass
    code=re.search(r"(?<![A-Z0-9])([GDCZTK]\s*\d{1,5})(?!\d)",text)
    if code: fields["train_number"]=re.sub(r"\s+","",code[1])
    arrival=re.search(r"(?:到达|到站|抵达)[：:\s]*(铜仁南站|铜仁站)",text)
    if arrival: fields["arrival_station"]=arrival[1]
    arrived=re.search(r"(?:到达时间|到站时间|抵达时间)[：:\s]*([01]\d|2[0-3])[:：]([0-5]\d)",text)
    if arrived: fields["arrival_time"]=":".join(arrived.groups())
    return fields


def recognize(jpeg):
    binary=shutil.which(os.getenv("TRAVEL_OCR_BINARY","tesseract"))
    if not binary: return {}
    try:
        process=subprocess.run([binary,"stdin","stdout","-l","chi_sim+eng","--psm","6"],input=jpeg,
                               capture_output=True,timeout=12,check=True)
        return suggested_fields(process.stdout.decode("utf-8",errors="replace"))
    except (OSError,subprocess.SubprocessError):
        return {}
