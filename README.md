# Employee Attendance & Analytics API

A FastAPI and MongoDB backend for employee attendance management. The API supports employee records, punch-in and
punch-out workflows, manual attendance corrections with an audit trail, and MongoDB-backed attendance analytics.

## Features

- Employee creation and paginated employee listing
- Punch-in and punch-out with attendance business rules
- IST calendar-date handling, including overnight shifts
- Late minutes, work hours, overtime, and half-day calculations
- Manual regularization with append-only correction history
- Attendance filtering and pagination
- Employee monthly summaries
- Department summaries with zero-log headcount support
- Late-comer leaderboard with standard competition ranking
- Department trends with gap-filled dates and moving averages
- MongoDB query explain endpoint
- Atomic uniqueness and concurrency protection through MongoDB indexes

## Requirements

- Python 3.10 or newer
- MongoDB 6.0 or newer
- MongoDB Atlas M0 is also supported

## Project structure

```text
app/
  main.py              FastAPI application
  __init__.py          Python package marker
sample_data/           Sample MongoDB Extended JSON documents
sample_seed.py         Loads sample data into MongoDB
requirements.txt       Python dependencies
openapi.yaml           API contract and business rules
DATA_MODEL.md          MongoDB document model
REVIEW.md              Review of the starter implementation
DECISIONS.md           Design decisions
```

## Installation

### Windows PowerShell

```powershell
Set-Location "C:\path\to\SDT_candidate_kit"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Configuration

Create a local `.env` file in the project root:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

For MongoDB Atlas, use the connection string supplied by Atlas:

```env
MONGO_URI=mongodb+srv://<username>:<password>@<cluster-host>/?retryWrites=true&w=majority
MONGO_DB=attendance_db
```

Never commit `.env` or real credentials. The repository's `.gitignore` excludes `.env` and `.venv/`.

## Start MongoDB

MongoDB must be running before starting the API. For a local MongoDB installation, start the MongoDB service normally.
If Docker is available, MongoDB 7 can be started with:

```bash
docker run --name attendance-mongo -p 27017:27017 -d mongo:7
```

## Load sample data

After MongoDB is running and the virtual environment is active:

```bash
python sample_seed.py
```

This clears and reloads the `employees` and `attendance_logs` collections using the documents in `sample_data/`.

## Run the API

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

For a normal run without auto-reload:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Documentation and health check

Once the server is running:

- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
- OpenAPI JSON: http://127.0.0.1:8000/openapi.json
- Health check: http://127.0.0.1:8000/health

PowerShell health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Expected response:

```json
{
  "status": "ok"
}
```

The health endpoint returns `503` when MongoDB cannot be reached.

## Main endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Check API and MongoDB readiness |
| `POST` | `/employees` | Create an employee |
| `GET` | `/employees` | List employees |
| `POST` | `/attendance/punch-in` | Record a punch-in |
| `POST` | `/attendance/punch-out` | Record a punch-out |
| `GET` | `/attendance` | Filter and paginate attendance |
| `PATCH` | `/attendance/{emp_code}/{date}` | Correct an attendance record |
| `GET` | `/analytics/employees/{emp_code}/monthly` | Employee monthly summary |
| `GET` | `/analytics/departments/summary` | Department monthly summary |
| `GET` | `/analytics/leaderboard/late` | Late-comers leaderboard |
| `GET` | `/analytics/departments/{department}/trend` | Daily department trend |
| `GET` | `/admin/explain/{endpoint}` | Inspect MongoDB execution plans |

The complete request and response contract is in [openapi.yaml](./openapi.yaml).

## Example requests

Create an employee:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/employees `
  -ContentType "application/json" `
  -Body '{"emp_code":"EMP1001","name":"Asha Rao","email":"asha@example.com","department":"Engineering","joined_on":"2026-07-01"}'
```

Punch in using epoch milliseconds:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/attendance/punch-in `
  -ContentType "application/json" `
  -Body '{"emp_code":"EMP1001","punched_at":1783312500000,"status":"PRESENT"}'
```

For interactive testing, use Swagger UI at `/docs`.

## Validation

Compile the application:

```powershell
python -m py_compile app/main.py
```

The application creates its MongoDB indexes during startup. Database-backed endpoint testing requires a running
MongoDB instance.

## Submission contents

The public repository should contain:

```text
app/main.py
requirements.txt
REVIEW.md
DECISIONS.md
README.md
```

Supporting files such as `openapi.yaml`, `DATA_MODEL.md`, `sample_seed.py`, and `sample_data/` may also be retained
for reproducibility.

Do not commit:

- `.env` files
- passwords, real credentials, or other secrets
- `.venv/`
- a Dockerfile
