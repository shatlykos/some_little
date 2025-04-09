# Примеры использования f-строк в Python

# 1. Простая вставка переменных
name = "Anna"
age = 30
print(f"{name} is {age} years old")

# 2. Выражения внутри f-строк
x = 5
y = 3
print(f"{x} + {y} = {x + y}")

# 3. Форматирование чисел
price = 12.3456
print(f"Цена: {price:.2f} руб.")

# 4. Выравнивание текста
name = "John"
print(f"|{name:<10}|")  # по левому краю
print(f"|{name:>10}|")  # по правому краю
print(f"|{name:^10}|")  # по центру

# 5. Числа с разделителями
big_number = 1000000
print(f"{big_number:,}")  # вывод с запятыми

# 6. Вставка кавычек
name = "Alice"
print(f'He said, "{name} is awesome!"')
