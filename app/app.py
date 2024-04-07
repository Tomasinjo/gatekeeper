from flask import Flask, request
import yaml
import os
from ipaddress import ip_network, ip_address
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

DEFAULT_SOURCE_RANGE = os.getenv('DEFAULT_SOURCE_RANGE', '').split(',')
MAX_IP_LEN = os.getenv('MAX_ALLOWED_IPS', 10)
whitelist = {
    'http': {
        'middlewares': {
            'dynamic-whitelist': {
                'IPAllowList': {
                    'sourceRange': []}
            }
        }
    }
}

def save_whitelist(approved_ips=[]):
    whitelist['http']['middlewares']['dynamic-whitelist']['IPAllowList']['sourceRange'] = approved_ips + DEFAULT_SOURCE_RANGE
    with open('dynamic-whitelist.yml', 'w') as f:
        yaml.dump(whitelist, f)

class Persistent:
    def __init__(self):
        self.approved_ips = []

p = Persistent()
save_whitelist()


@app.route('/', defaults={'path': ''}, methods=['GET', 'POST'])
@app.route('/<path:path>', methods=['GET', 'POST'])
def catch_all(path):
    resp = 'OK'
    source_ip = request.headers.get('X-Forwarded-For')
    # prevent whitelisting IPs that are defined in default network ranges
    if len([net for net in DEFAULT_SOURCE_RANGE if ip_address(source_ip) in ip_network(net)]) > 0:
        return resp 
    if source_ip not in p.approved_ips:
        app.logger.info(f'New IP {source_ip} authenticated, adding to whitelist')
        p.approved_ips.append(source_ip)
        current_ip_len = len(p.approved_ips)
        if current_ip_len > MAX_IP_LEN:
            app.logger.info(f'Max nmbr of whitelisted IPs hit, removing the oldest')
            p.approved_ips = p.approved_ips[current_ip_len - MAX_IP_LEN:]
        save_whitelist(p.approved_ips)
    return resp

if __name__ == "__main__":
    app.run(host='0.0.0.0')