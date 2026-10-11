# DECISIONS.md

1. **Indexes.** I create a unique `employees.emp_code` index for the business-key guarantee, an employee
department/joined-on index for headcount lookups, a unique `attendance_logs.(emp_code,date)` index for one record per
attendance day, a `(date desc, emp_code asc)` index for the paged attendance list, and an
`(emp_code,date,punch_in desc)` index for punch-out selection. I considered a standalone status index, but rejected it:
status is not selective enough for the main queries and adding it would not support their sort.

2. **Punch-in race.** Both requests read the employee and compute the same attendance date. They then insert the same
natural key. MongoDB's unique compound index accepts one insert and rejects the other with `DuplicateKeyError`; the
first returns 201 and the loser returns 409. There is no check-then-insert decision that can race.

3. **Ties.** Rows are sorted by total late minutes and employee code. The assigned rank is the row's first position
for each distinct total, so ties share a standard competition rank and the next rank is skipped. Filtering happens
after ranking, therefore every employee tied at a rank at or below `limit` is returned.

4. **Headcount.** The department summary starts from employees whose `joined_on` is no later than the month end and
uses a lookup that preserves an empty log array. The grouping increments headcount before iterating logs, so an employee
with no attendance documents still contributes one.

5. **One change at 100x.** I would materialize daily/monthly attendance facts (or pre-aggregate them incrementally) and
cache the bounded trend/summary reads. That avoids repeatedly joining and grouping the raw log collection while retaining
the raw records for audit.
