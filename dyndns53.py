#!/usr/bin/env python
# -*- encoding: utf-8 -*-

from __future__ import print_function
import boto3
import sys
import re
import json
import base64

import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class AuthorizationMissing(Exception):
    status = 401
    response = {"WWW-Authenticate": "Basic realm=dyndns53"}


class HostnameException(Exception):
    status = 404
    response = "nohost"


class AuthorizationException(Exception):
    status = 403
    response = "badauth"


class FQDNException(Exception):
    status = 400
    response = "notfqdn"


class BadAgentException(Exception):
    status = 400
    response = "badagent"


class AbuseException(Exception):
    status = 403
    response = "abuse"


conf = {
    'username:password': {
        'hosts': {
            'domain.io.': {
                'aws_region': 'us-west-2',
                'zone_id': 'Z1X3TIORS078SW',
                'record': {
                    'ttl': 60,
                    'type': 'A',
                },
                'last_update': None,
            },
            't.domain.io.': {
                'aws_region': 'us-west-2',
                'zone_id': 'Z1X3TIORS078SW',
                'record': {
                    'ttl': 60,
                    'type': 'A',
                },
                'last_update': None,
            },
            'm.domain.io.': {
                'aws_region': 'us-west-2',
                'zone_id': 'Z1X3TIORS078SW',
                'record': {
                    'ttl': 60,
                    'type': 'A',
                },
                'last_update': None,
            },
            's.domain.io.': {
                'aws_region': 'us-west-2',
                'zone_id': 'Z1X3TIORS078SW',
                'record': {
                    'ttl': 60,
                    'type': 'A',
                },
                'last_update': None,
            },
            'e.domain.io.': {
                'aws_region': 'us-west-2',
                'zone_id': 'Z1X3TIORS078SW',
                'record': {
                    'ttl': 60,
                    'type': 'A',
                },
                'last_update': None,
            },
            'vouch.domain.io.': {
                'aws_region': 'us-west-2',
                'zone_id': 'Z1X3TIORS078SW',
                'record': {
                    'ttl': 60,
                    'type': 'A',
                },
                'last_update': None,
            },
        },
    },
}


re_ip = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def _parse_ip(ipstring):
    m = re_ip.match(ipstring)
    if bool(m) and all(map(lambda n: 0 <= int(n) <= 255, m.groups())):
        return ipstring
    else:
        raise BadAgentException("Invalid IP string: {}".format(ipstring))


client53 = boto3.client('route53', 'us-west-2')


def r53_upsert(host, hostconf, ip):

    record_type = hostconf['record']['type']

    record_set = client53.list_resource_record_sets(
        HostedZoneId=hostconf['zone_id'],
        StartRecordName=host,
        StartRecordType=record_type,
        MaxItems='1'
    )

    old_ip = None
    if not record_set:
        msg = "No existing record found for host {} in zone {}"
        logger.info(msg.format(host, hostconf['zone_id']))
    else:
        record = record_set['ResourceRecordSets'][0]
        if record['Name'] == host and record['Type'] == record_type:
            if len(record['ResourceRecords']) == 1:
                for subrecord in record['ResourceRecords']:
                    old_ip = subrecord['Value']
            else:
                msg = "Multiple existing records found for host {} in zone {}"
                raise ValueError(msg.format(host, hostconf['zone_id']))
        else:
            msg = "No existing record found for host {} in zone {}"
            logger.info(msg.format(host, hostconf['zone_id']))

    if old_ip == ip:
        logger.debug("Old IP same as new IP: {}".format(ip))
        return False, "not updated"

    logger.debug("Old IP was: {}".format(old_ip))
    return_status = client53.change_resource_record_sets(
        HostedZoneId=hostconf['zone_id'],
        ChangeBatch={
            'Changes': [
                    {
                        'Action': 'UPSERT',
                        'ResourceRecordSet': {
                            'Name': host,
                            'Type': hostconf['record']['type'],
                            'TTL':  hostconf['record']['ttl'],
                            'ResourceRecords': [
                                {
                                    'Value': ip
                                }
                            ]
                        }
                    }
            ]
        }
    )

    return True, "updated"


def _handler(event, context):

    if 'header' not in event:
        msg = "Headers not populated properly. Check API Gateway configuration."
        raise KeyError(msg)

    try:
        auth_header = event['header']['Authorization']
    except KeyError as e:
        raise AuthorizationMissing("Authorization required but not provided.")

    try:
        # trim off "Basic " prefix from auth_string, then base64 decode, then encode as a utf-8 str and split on ":" to return auth_user and auth_pass
        auth_user, auth_pass = base64.b64decode(
            auth_header[6:]).decode('utf-8').split(':')
    except Exception as e:
        raise e

    auth_string = ':'.join([auth_user, auth_pass])
    if auth_string not in conf:
        raise AuthorizationException("Bad username/password.")

    try:
        hosts = set(h if h.endswith('.') else h+'.' for h in
                    event['querystring']['hostname'].split(','))
    except KeyError as e:
        raise BadAgentException("Hostname(s) required but not provided.")

    print(hosts)
    if any(host not in conf[auth_string]['hosts'] for host in hosts):
        raise HostnameException()

    try:
        ip = _parse_ip(event['querystring']['myip'])
        logger.debug("User supplied IP address: {}".format(ip))
    except KeyError as e:
        ip = _parse_ip(event['context']['source-ip'])
        msg = "User omitted IP address, using best-guess from $context: {}"
        logger.debug(msg.format(ip))

    results = [(host, r53_upsert(host, conf[auth_string]['hosts'][host], ip)) for host in hosts]
    updated_hosts = [host for host, (updated, msg) in results if updated]
    unchanged_hosts = [host for host, (updated, msg) in results if not updated]
    
    if updated_hosts:
        return "good " + ', '.join(updated_hosts) + f" with IP {ip}"
    if unchanged_hosts:
        return "nochg " + ', '.join(unchanged_hosts) + f" with IP {ip}"
    return "nochg"


def lambda_handler(event, context):

    try:

        response = _handler(event, context)

    except Exception as e:
        try:
            j = {'status': e.status, 'response': e.response,
                 'additional': e.message}
        except AttributeError as f:
            j = {'status': 500, 'response': "911", 'additional': str(e)}
        finally:
            raise e

    return {'status': 200, 'response': response}
