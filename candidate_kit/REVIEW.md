# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | It uses naive datetimes and does not account for overnight shifts. The request path also accepts timestamps without the contract's epoch-millisecond and whole-second normalization. | A UTC/IST boundary, fractional millisecond input, or a 22:00-06:00 employee produces the wrong late value. | Validate epoch milliseconds at the Pydantic boundary, convert them to truncated UTC datetimes, calculate the employee's local shift start, and apply the strict ten-minute grace rule. |
| 2 | `compute_work_hours` | Python float rounding is banker's rounding rather than the required half-up rounding. | A duration whose third decimal is exactly 5 can return a different value from the contract. | Use `Decimal.quantize(..., ROUND_HALF_UP)`. |
| 3 | `compute_overtime` | It builds a date with the wrong timezone assumptions and ignores overnight shift ends and the 30-minute threshold. | An overnight punch-out or an overtime duration below 30 minutes is misreported. | Build the shift end from the attendance date and employee shift, then floor minutes and zero values below 30. |
| 4 | `create_employee` | Duplicate protection is a check-then-insert race and `created_at` is not exposed as epoch milliseconds. | Concurrent creates can both pass the check; the response contains a datetime. | Create a unique index and handle `DuplicateKeyError`; serialize instants at the API boundary. |
| 5 | `list_employees` | `skip` starts at `page * page_size`, `total` ignores the department filter, and there is no deterministic sort. | Page 1 omits the first page, filtered totals are too large, and ordering can vary. | Use `(page - 1) * page_size`, count the filtered query, and sort by `emp_code`. |
| 6 | `punch_in` | Unknown employees cause a `None` dereference; timestamps use local naive time and the check-then-insert race remains; it also stores no normalized derived shape. | An unknown code raises a server error; concurrent requests create duplicates; epoch/IST tests fail. | Validate the employee, normalize to whole-second UTC, derive the R1/R2 date and fields, and rely on the unique compound index. |
| 7 | `list_attendance` | It loads and sorts the entire collection in Python, has no date validation, and fails to normalize legacy/mongo fields consistently. | A large dataset is slow or memory-heavy; invalid ranges are accepted. | Push filter, sort, skip, limit, and count to MongoDB and serialize each page only. |
| 8 | `health` | It always returns 200 without checking whether MongoDB is reachable. | Stop MongoDB and call `/health`; the starter still reports `{"status": "ok"}`. | Run MongoDB's `ping` command and return 503 when the database is unavailable. |
| 9 | Request models and pagination | The starter models do not enforce the contract's formats, enums, ranges, or required field constraints. Pagination accepts invalid values, and punch-in accepts statuses that are not presence statuses. | Invalid employee codes, dates, emails, epoch values, statuses, or page sizes are accepted or fail later with the wrong response. | Added Pydantic validation and FastAPI query constraints so invalid input receives 422. |
| 10 | Missing contract endpoints | Punch-out, regularization, all analytics, and explain were absent. | Requests to the documented paths return 404. | Implemented all remaining paths, including aggregation-backed reads and audit history. |

The starter's use of MongoDB collections and FastAPI routing was not itself a defect; the collection names and
employee/attendance document relationship match the supplied data model. The health endpoint's response shape was
also retained, but it now pings MongoDB as required by the contract.

Also note anything you looked at and decided was **not** a defect, and why.
