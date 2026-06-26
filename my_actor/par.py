my_list = ['a', 'b', 'c', 'd']

# Loop continues until the list is empty
while my_list:
    item = my_list.pop()
    print(f"Processed: {item}")

print(f"Final list: ", type(my_list))
