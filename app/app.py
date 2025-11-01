from flask import Flask, request
import yaml
import os
from ipaddress import ip_network, ip_address
from pathlib import Path
import logging
import requests
from time import sleep

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

def save_whitelist(approved_ips:list = []) -> None:
    whitelist['http']['middlewares']['dynamic-whitelist']['IPAllowList']['sourceRange'] = approved_ips + DEFAULT_SOURCE_RANGE
    with open('dynamic-whitelist.yml', 'w') as f:
        yaml.dump(whitelist, f)

def is_valid_ip(address: str) -> bool:
    try:
        ip_address(address)
        return True
    except ValueError:
        return False


class Persistent:
    def __init__(self):
        self.approved_ips = []

p = Persistent()
save_whitelist()

def is_share_link_valid(url: str) -> bool:
   status_code = requests.get(url).status_code
   if status_code >= 200 and status_code < 300:
       return True
   return False

def add_source_to_whitelist(ip: str) -> None:
    if not is_valid_ip(address=ip):
        return
    if '%' in ip:
        return
    # prevent whitelisting IPs that are defined in default network ranges
    if len([net for net in DEFAULT_SOURCE_RANGE if ip_address(ip) in ip_network(net)]) > 0:
        return
    if ip not in p.approved_ips:
        app.logger.info(f'New IP {ip} authenticated, adding to whitelist')
        p.approved_ips.append(ip)
        current_ip_len = len(p.approved_ips)
        if current_ip_len > MAX_IP_LEN:
            app.logger.info(f'Max nmbr of whitelisted IPs hit, removing the oldest')
            p.approved_ips = p.approved_ips[current_ip_len - MAX_IP_LEN:]
        save_whitelist(p.approved_ips)


@app.route('/verify_share_request')
def verify_share_request():
    proto = request.args.get('protocol')
    service = request.args.get('container_name_port')
    original_uri = request.headers.get('X-Forwarded-Uri')

    url_to_check = f'{proto}://{service}{original_uri}'
    if is_share_link_valid(url_to_check):
        app.logger.info(f'Got request for valid share: {url_to_check}')
        add_source_to_whitelist(ip=request.headers.get('X-Forwarded-For'))
        sleep(3) # not great, but traefik needs some time to refresh the updated whitelist
        return '', 200
    app.logger.info(f'Got request for invalid share: {url_to_check}')
    return '', 403


@app.route('/', defaults={'path': ''}, methods=['GET', 'POST'])
@app.route('/<path:path>', methods=['GET', 'POST'])
def catch_all(path):
    resp = 'OK' # response doesnt matter for mirrored requests
    add_source_to_whitelist(ip=request.headers.get('X-Forwarded-For'))
    return resp


if __name__ == "__main__":
    app.run(host='0.0.0.0')