from datetime import datetime, timedelta


def count_days(start_date, end_date):
    start_date = datetime.strptime(start_date, '%Y-%m-%d')
    end_date = datetime.strptime(end_date, '%Y-%m-%d')
    
    if start_date.month == end_date.month and start_date.year == end_date.year:
        return (end_date - start_date).days + 1
    else:
        last_day_of_month = (start_date.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return (last_day_of_month - start_date).days + 1


print(count_days('2022-08-01', '2023-08-01'))  # Output: 31
print(count_days('2022-02-01', '2022-09-28'))  # Output: 28
