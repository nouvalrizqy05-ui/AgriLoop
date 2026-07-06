# ML service dev server - Windows PowerShell
# Run from project root: .\ml_service\run.ps1
# (uvicorn --app-dir adds ml_service\ to sys.path so flat imports work)
$env:PYTHONIOENCODING = "utf-8"   # V2 source pakai emoji di print(); cegah cp1252 crash
& .\.venv\Scripts\Activate.ps1
& python -m uvicorn main:app --reload --port 8000 --host 0.0.0.0 --app-dir ml_service
