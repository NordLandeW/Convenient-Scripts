import random
import unicodedata
import pyperclip

def generate_unicode_string(length):
    def is_printable(char):
        category = unicodedata.category(char)
        return not (category.startswith('C') or category.startswith('Z') and category != 'Zs')

    printable_chars = [chr(i) for i in range(0x110000) if is_printable(chr(i))]
    return ''.join(random.choice(printable_chars) for _ in range(length))

length = int(input("请输入要生成的Unicode字符串长度: "))
unicode_string = generate_unicode_string(length)
print(unicode_string)

pyperclip.copy(unicode_string)
