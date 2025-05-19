import base64
import concurrent.futures
import hashlib
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import base58
import requests
from Cryptodome.Cipher import DES
from Cryptodome.Util import Padding

import argparse
import tarfile
import random

import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别
    format='%(asctime)s - %(levelname)s - %(message)s')  # 设置日志格式

_apiBaseUrl = 'https://www.wenshushu.cn'


def patch_session_headers(session):
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0'
    ]
    ua = random.choice(user_agents)
    common_headers = {
        "Origin": "https://www.wenshushu.cn",
        "Priority": "u=1, i",
        "Prod": "com.wenshushu.web.pc",
        "Referer": "https://www.wenshushu.cn/",
        "Sec-Ch-Ua": '"Microsoft Edge";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": 'empty',
        "Sec-Fetch-Mode": 'cors',
        "Sec-Fetch-Site": 'same-origin',
        "X-TOKEN": "",
        "User-Agent": ua,
        "Accept-Language": "zh-CN, en-um;q=0.9"  # NOTE: require header, otherwise return {"code":-1, ...}
    }
    session.headers.update(common_headers)
    session.headers['X-TOKEN'] = login_anonymous(session)


def login_anonymous(session):
    r = session.post(
        url=_apiBaseUrl + '/ap/login/anonymous',
        json={
            "dev_info": "{}"
        }
    )
    api_overseashow(session)
    return r.json()['data']['token']


def api_overseashow(session):
    r = session.post(
        url=_apiBaseUrl + '/ap/user/overseashow',
        json={
            "lang": "zh"
        }
    )


def download(session, args):
    url = args.url

    def get_tid(token):
        r = session.post(
            url=_apiBaseUrl + '/ap/task/token',
            json={
                'token': token
            }
        )
        return r.json()['data']['tid']

    def mgrtask(tid):
        r = session.post(
            url=_apiBaseUrl + '/ap/task/mgrtask',
            json={
                'tid': tid,
                'password': ''
            }
        )
        rsp = r.json()
        expire = rsp['data']['expire']
        days, remainder = divmod(int(float(expire)), 3600 * 24)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        logging.info(f'文件过期时间:{days}天{hours}时{minutes}分{seconds}秒')

        file_size = rsp['data']['file_size']
        logging.info(f'文件大小:{round(int(file_size) / 1024 ** 2, 2)}MB')
        return rsp['data']['boxid'], rsp['data']['ufileid']  # pid

    def list_file(tid):
        bid, pid = mgrtask(tid)
        r = session.post(
            url=_apiBaseUrl + '/ap/ufile/list',
            json={
                "start": 0,
                "sort": {
                    "name": "asc"
                },
                "bid": bid,
                "pid": pid,
                "type": 1,
                "options": {
                    "uploader": "true"
                },
                "size": 50
            }
        )
        rsp = r.json()
        filelist = rsp['data']['fileList']
        for i, file_info in enumerate(filelist):
            filename = file_info['fname']
            fid = file_info['fid']
            print(f'[{i + 1}/{len(filelist)}] 文件名:{filename}')
            sign(bid, fid, filename)

    def down_handle(url, filename):
        logging.info('开始下载!')
        r = session.get(url, stream=True)
        dl_size = int(r.headers.get('Content-Length'))
        block_size = 2097152
        dl_count = 0
        with open(filename, 'wb') as f:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=block_size):
                f.write(chunk)
                dl_count += len(chunk)
                logging.info(f'下载进度:{int(dl_count / dl_size * 100)}%')
            logging.info('下载完成:100%')

    def sign(bid, fid, filename):
        r = session.post(
            url=_apiBaseUrl + '/ap/dl/sign',
            json={
                'consumeCode': 0,
                'type': 1,
                'ufileid': fid
            }
        )
        if r.json()['data']['url'] == "" and \
                r.json()['data']['ttNeed'] != 0:
            logging.warning("对方的分享流量不足")
            sys.exit(0)
        url = r.json()['data']['url']
        down_handle(url, filename)

    if len(url.split('/')[-1]) == 16:
        token = url.split('/')[-1]
        tid = get_tid(token)
    elif len(url.split('/')[-1]) == 11:
        tid = url.split('/')[-1]

    list_file(tid)


def upload(session, args):
    filePath = ''
    need_del_file = False

    def make_tar_gz(output_filename, source_dir):
        with tarfile.open(output_filename, "w:gz") as tar:
            tar.add(source_dir, arcname=os.path.basename(source_dir))

    def get_readable_size(file_path):
        size = os.path.getsize(file_path)  # 获取文件大小（字节）
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} PB"

    if not os.path.exists(args.path):
        logging.error(f'{args.path} 不存在')
        return
    if os.path.isdir(args.path):
        args.path = os.path.normpath(args.path)
        filePath = os.path.basename(args.path) + '.tar.gz'
        start = time.time()
        logging.warning(f'{args.path} 是一个目录,自动压缩为: {filePath}')
        make_tar_gz(filePath, args.path)
        end = time.time()
        logging.info(f"压缩耗时: {end - start:.4f} 秒,大小: {get_readable_size(filePath)}")
        need_del_file = True
    elif os.path.isfile(args.path):
        filePath = args.path
        need_del_file = False

    chunk_size = 2048 * 1024
    file_size = os.path.getsize(filePath)
    ispart = True if file_size > chunk_size else False

    def read_file(block_size=chunk_size):
        partnu = 0
        with open(filePath, "rb") as f:
            while True:
                block = f.read(block_size)
                partnu += 1
                if block:
                    yield block, partnu
                else:
                    return

    def sha1_str(s):
        cm = hashlib.sha1(s.encode()).hexdigest()
        return cm

    def calc_file_hash(hashtype, block=None):
        read_size = chunk_size if ispart else None
        if not block:
            with open(filePath, 'rb') as f:
                block = f.read(read_size)
        if hashtype == "MD5":
            hash_code = hashlib.md5(block).hexdigest()
        elif hashtype == "SHA1":
            hash_code = hashlib.sha1(block).hexdigest()
        return hash_code

    def get_epochtime():
        r = session.get(
            url=_apiBaseUrl + '/ag/time'
        )
        rsp = r.json()
        return rsp["data"]["time"]  # epochtime expires in 60s

    def get_cipherheader(epochtime, token, data):
        # cipherMethod: DES/CBC/PKCS7Padding
        json_dumps = json.dumps(data, ensure_ascii=False)
        md5_hash_code = hashlib.md5((json_dumps + token).encode()).hexdigest()
        base58_hash_code = base58.b58encode(md5_hash_code)
        key_iv = (
            # 时间戳逆序取5位并作为时间戳字串索引再次取值，最后拼接"000"
                "".join([epochtime[int(i)] for i in epochtime[::-1][:5]]) + "000"
        ).encode()
        cipher = DES.new(key_iv, DES.MODE_CBC, key_iv)
        cipherText = cipher.encrypt(
            Padding.pad(base58_hash_code, DES.block_size, style="pkcs7")
        )
        return base64.b64encode(cipherText)

    def storage():
        r = session.post(
            url=_apiBaseUrl + '/ap/user/storage',
            json={}
        )
        rsp = r.json()
        rest_space = int(rsp['data']['rest_space'])
        send_space = int(rsp['data']['send_space'])
        storage_space = rest_space + send_space
        logging.info('当前已用空间:{}GB,剩余空间:{}GB,总空间:{}GB'.format(
            round(send_space / 1024 ** 3, 2),
            round(rest_space / 1024 ** 3, 2),
            round(storage_space / 1024 ** 3, 2)
        ))

    def userinfo():
        r = session.post(
            url=_apiBaseUrl + '/ap/user/userinfo',
            json={"plat": "pcweb"}
        )
        rsp = r.json()
        return rsp['data']

    def msg():
        session.post(
            url=_apiBaseUrl + '/ap/user/msg',
            json={}
        )

    def getnotice():
        session.post(
            url=_apiBaseUrl + '/ap/opr/getnotice',
            json={}
        )

    def get_zip_unzip_process(user_data):
        session.post(
            url=_apiBaseUrl + '/ap/ufile/get_zip_unzip_process',
            json={
                "uid": user_data['devide_id']
            }
        )

    def addsend():
        user_data = userinfo()
        storage()
        # msg()
        # getnotice()
        # get_zip_unzip_process(user_data)

        epochtime = get_epochtime()
        req_data = {
            "sender": "",
            "remark": "",
            "isextension": False,
            "notSaveTo": False,
            "notDownload": False,
            "notPreview": False,
            "downPreCountLimit": 0,
            "trafficStatus": 0,
            "pwd": args.pwd,
            "expire": "1",
            "recvs": [
                "social",
                "public"
            ],
            "file_size": file_size,
            "file_count": 1,
            "fileDisplay": 0,
            "task_traffic_limit": ""
        }
        # POST的内容在服务端会以字串形式接受然后直接拼接X-TOKEN，不会先反序列化JSON字串再拼接
        # 加密函数中的JSON序列化与此处的JSON序列化的字串形式两者必须完全一致，否则校验失败
        r = session.post(
            url=_apiBaseUrl + '/ap/task/addsend',
            json=req_data,
            headers={
                "A-Code": get_cipherheader(epochtime, session.headers['X-TOKEN'], req_data),
                "Req-Time": epochtime
            }
        )
        rsp = r.json()
        if rsp["code"] == 1021:
            logging.warning(f'操作太快啦！请{rsp["message"]}秒后重试')
            sys.exit(0)
        data = rsp["data"]
        assert data, "需要滑动验证码"
        bid, ufileid, tid = data["bid"], data["ufileid"], data["tid"]
        upId = get_up_id(bid, ufileid, tid, file_size)
        return bid, ufileid, tid, upId

    def get_up_id(bid: str, ufileid: str, tid: str, file_size: int):
        r = session.post(
            url=_apiBaseUrl + "/ap/uploadv2/getupid",
            json={
                "preid": ufileid,
                "boxid": bid,
                "linkid": tid,
                "utype": "sendcopy",
                "originUpid": "",
                "length": file_size,
                "count": 1
            }
        )
        return r.json()["data"]["upId"]

    def psurl(fname, upId, file_size, partnu=None):
        payload = {
            "ispart": ispart,
            "fname": fname,
            "fsize": file_size,
            "upId": upId,
        }
        if ispart:
            payload["partnu"] = partnu
        r = session.post(
            url=_apiBaseUrl + "/ap/uploadv2/psurl",
            json=payload
        )
        rsp = r.json()
        url = rsp["data"]["url"]  # url expires in 600s (10 minutes)
        return url

    def copysend(boxid, taskid, preid):
        r = session.post(
            url=_apiBaseUrl + '/ap/task/copysend',
            json={
                'bid': boxid,
                'tid': taskid,
                'ufileid': preid
            }
        )
        rsp = r.json()
        logging.info(f"个人管理链接：{rsp['data']['mgr_url']}")
        logging.info(f"公共链接：{rsp['data']['public_url']}, 取件码：{args.pwd or ''}")

    def fast():
        boxid, preid, taskid, upId = addsend()
        cm1, cs1 = calc_file_hash("MD5"), calc_file_hash("SHA1")
        cm = sha1_str(cm1)
        name = os.path.basename(filePath)

        payload = {
            "hash": {
                "cm1": cm1,  # MD5
                "cs1": cs1,  # SHA1
            },
            "uf": {
                "name": name,
                "boxid": boxid,
                "preid": preid
            },
            "upId": upId
        }

        if not ispart:
            payload['hash']['cm'] = cm  # 把MD5用SHA1加密
        for _ in range(2):
            r = session.post(
                url=_apiBaseUrl + '/ap/uploadv2/fast',
                json=payload
            )
            rsp = r.json()
            can_fast = rsp["data"]["status"]
            ufile = rsp['data']['ufile']
            if can_fast and not ufile:
                hash_codes = ''
                for block, _ in read_file():
                    hash_codes += calc_file_hash("MD5", block)
                payload['hash']['cm'] = sha1_str(hash_codes)
            elif can_fast and ufile:
                logging.info(f'文件 {name} 可以被秒传！')
                getprocess(upId)
                copysend(boxid, taskid, preid)
                sys.exit(0)

        return name, taskid, boxid, preid, upId

    def getprocess(upId: str):
        while True:
            r = session.post(
                url=_apiBaseUrl + "/ap/ufile/getprocess",
                json={
                    "processId": upId
                }
            )
            if r.json()["data"]["rst"] == "success":
                return True
            time.sleep(1)

    def complete(fname, upId, tid, boxid, preid):
        session.post(
            url=_apiBaseUrl + "/ap/uploadv2/complete",
            json={
                "ispart": ispart,
                "fname": fname,
                "upId": upId,
                "location": {
                    "boxid": boxid,
                    "preid": preid
                }
            }
        )
        copysend(boxid, tid, preid)

    def file_put(psurl_args, fn, offset=0, read_size=chunk_size):
        with open(fn, "rb") as fio:
            fio.seek(offset)
            requests.put(url=psurl(*psurl_args), data=fio.read(read_size))

    def upload_main():
        fname, tid, boxid, preid, upId = fast()
        if ispart:
            logging.info('文件正在被分块上传！')
            with ThreadPoolExecutor(max_workers=4) as executor:  # or use os.cpu_count()
                future_list = []
                for i in range((file_size + chunk_size - 1) // chunk_size):
                    ul_size = chunk_size if chunk_size * (i + 1) <= file_size \
                        else file_size % chunk_size
                    future_list.append(executor.submit(
                        file_put, [fname, upId, ul_size, i + 1],
                        filePath, chunk_size * i, ul_size
                    ))
                future_length = len(future_list)
                count = 0
                for _ in concurrent.futures.as_completed(future_list):
                    count += 1
                    sp = count / future_length * 100
                    logging.info(f'分块进度:{int(sp)}%')
                    if sp == 100:
                        logging.info('上传完成:100%')
        else:
            logging.info('文件被整块上传！')
            file_put([fname, upId, file_size], filePath, 0, file_size)
            logging.info('上传完成:100%')

        # 对于目录上传，上传完成后需要删除临时文件
        if need_del_file:
            os.remove(filePath)
            logging.warning(f'删除临时文件: {filePath}')

        complete(fname, upId, tid, boxid, preid)
        getprocess(upId)

    upload_main()


def main():
    def random_pwd():
        num = random.randint(1000, 9999)
        return str(num)

    s = requests.Session()

    parser = argparse.ArgumentParser(description="文叔叔 CLI")
    subparsers = parser.add_subparsers(dest='command', required=True)

    # upload
    upload_parser = subparsers.add_parser('upload', help='上传文件')
    upload_parser.add_argument('path', nargs='?', help='要上传的路径(文件或目录,如果是目录会自动打包为 tar.gz)')
    upload_parser.add_argument('--pwd', required=False, help='指定取件码,4位数字')
    upload_parser.add_argument('--random-pwd', required=False, help='随机生成取件码,4位数字')
    upload_parser.add_argument('--proxy', help='设置代理(http/https/socks4/socks4a/socks45)，如 http://127.0.0.1:8080')

    # download
    download_parser = subparsers.add_parser('download', help='下载资源')
    download_parser.add_argument('--url', required=True, help='下载链接')
    download_parser.add_argument('--proxy', help='设置代理(http/https/socks4/socks4a/socks45)，如 http://127.0.0.1:8080')

    download_parser = subparsers.add_parser('version', help='版本')

    args = parser.parse_args()
    try:
        if hasattr(args, 'proxy') and args.proxy:
            proxies = {
                'http': args.proxy,
                'https': args.proxy
            }
            s.proxies.update(proxies)
            logging.info(f'使用代理: {args.proxy}')
        else:
            s.proxies = {}

        if args.command == 'upload':
            if args.pwd:
                args.pwd = args.pwd[:4]
            elif args.random_pwd:
                args.pwd = random_pwd()
            patch_session_headers(s)
            upload(s, args)
        elif args.command == 'download':
            patch_session_headers(s)
            download(s, args)
        elif args.command == 'version':
            print('v2.0.8')
    except Exception as e:
        traceback.print_exc()


if __name__ == "__main__":
    main()
