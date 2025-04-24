from fake_useragent import UserAgent

ua = UserAgent(platforms='desktop', min_version=100.0, os='Windows', browsers=['Edge', 'Chrome', 'Firefox'])

print('[')
for i in range(10):
    print("'"+ua.random+"',")
print(']')