from datetime import datetime, timedelta


def count_days():
    # Ask the user to input the start and end dates in ISO format
    start_date = input('Enter the start date (in ISO format YYYY-MM-DD): ')
    end_date = input('Enter the end date (in ISO format YYYY-MM-DD): ')
    
    # Convert the input strings to datetime objects
    start_date = datetime.strptime(start_date, '%Y-%m-%d')
    end_date = datetime.strptime(end_date, '%Y-%m-%d')
    
    # If the start and end dates are in the same month and year, calculate the difference between the dates
    if start_date.month == end_date.month and start_date.year == end_date.year:
        return (end_date - start_date).days + 1
    # If the start and end dates are not in the same month and year, calculate the difference between the start date and the last day of the month
    else:
        last_day_of_month = (start_date.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return (last_day_of_month - start_date).days + 1


print(count_days())  # Output: 31
