import requests

# Use your Web Unblocker credentials here.
USERNAME, PASSWORD = '_xMsTv_xMsTv', 'BdFRVpV~85vzy~'

# Define proxy dict.
proxies = {
  'http': f'http://{USERNAME}:{PASSWORD}@unblock.oxylabs.io:60000',
  'https': f'https://{USERNAME}:{PASSWORD}@unblock.oxylabs.io:60000',
}

response = requests.request(
    'GET',
    'https://www.indeed.com/jobs?q=machine+learning+engineer&start=10',
    verify=False,  # Ignore the SSL certificate
    proxies=proxies,
)

# Print result page to stdout
print(response.text)

# Save returned HTML to result.html file
with open('result.html', 'w') as f:
    f.write(response.text)