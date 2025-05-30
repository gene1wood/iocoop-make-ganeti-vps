#!/usr/bin/env python3
#
# Author: Ben Kochie <ben@nerp.net>

import json
import os
import time
import types
import urllib3
import ssl
import base64
from urllib3.exceptions import HTTPError
from functools import reduce

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
http = urllib3.PoolManager(cert_reqs='CERT_NONE')

user_instances = {}
auth_users = {}

auth_template = 'no-agent-forwarding,no-user-rc,no-X11-forwarding,'
keydir = '/root/make-vps/keys/'
outdir = '/root/vps/'

defaults = {'keydir': '/root/make-vps/keys/',
            'outdir': '/root/vps/',
            'ganeti_instance': 'ganeti:5080',
            'ganeti_auth': 'user:password'
            }
with open('/etc/make-vps.json') as f:
    config_file = json.load(f)

config = {**defaults, **config_file}

basic_auth_header = urllib3.make_headers(basic_auth=config['ganeti_auth'])

authorized_keys_file = config['outdir'] + "authorized_keys"
attributes_py_file = config['outdir'] + "attributes.py"

context = ssl._create_unverified_context()

def BaseURL():
  """Base URL for the version 2 Ganeti HTTP API."""
  return 'https://%s/2' % config['ganeti_instance']

def GetURL(url):
  """GET request for a URL, returning the response body as a string."""
  try:
    resp = http.request('GET', url, headers=basic_auth_header)
    return resp.data
  except HTTPError as error:
    raise Error(error)

def GetInstanceList():
  """Obtain the list of instances."""
  url = '%s/instances' % (BaseURL())
  return GetURL(url)

def GetNode(node):
  """Obtain the short information about the node."""
  url = '%s/instances/%s' % (BaseURL(), node)
  return GetURL(url)

def GetIdKeys(list, dict):
  """Get the 'id' key from a dict and add it to the list."""
  list.append(dict['id'])
  return list

def GetNodeInfoStatic(node):
  """Obtain the information about the system or a particular node."""
  url = '%s/instances/%s/info?static=1' % (BaseURL(), node)
  job_id = json.loads(GetURL(url))
  return GetJob(job_id, node)


def GetJob(job_id, node_name=None):
  """Poll a Job ID until it completes, returning the opresult JSON string."""
  url = '%s/jobs/%s' % (BaseURL(), job_id)
  for attempt in range(0, 10):
    data = json.loads(GetURL(url))
    if data['status'] not in ('queued', 'waiting', 'running'):
      if (type(data['opresult'][0]) is types.ListType and
          node_name in data['opresult'][0]):
        return IndentJsonString(data['opresult'][0][node_name], indent=1)
      elif data['opresult'][0]:
        return IndentJsonString(data['opresult'][0], indent=1)
      return ''
    time.sleep(max(.5**attempt - 1, 8))


def PostURL(url):
  """Post a URL request, returning the response body as a string."""
  return PutURL(url, pycurl.POST)


def IndentJsonString(json_string, indent=1):
  """Format a string of JSON data is an indented 1-per-line way."""
  return json.dumps(json_string, indent=indent)


def PermitOpen(port):
  """ Format a string to ssh authorized_keys permitopen """
  return 'permitopen="localhost:%s"' % port

instance_list = reduce(GetIdKeys, json.loads(GetInstanceList()), [])

for filename in instance_list:
  print("Processing file: " + filename)
  instance_info = json.loads(GetNode(filename))
  network_port = str(instance_info['network_port'])
  ssh_keys = set()
  try:
    with open(keydir+filename, 'r') as keyfile:
      for line in keyfile:
        if line.rstrip('\n') == '':
          continue
        keyline = line.rstrip('\n').split(' ')
        if len(keyline) < 3:
          print('Non conforming line in %s : "%s"' % (keydir+filename, line))
          continue # non conforming line
        elif not keyline[2] in auth_users:
          auth_users[keyline[2]] = { 'sshkey': (keyline[0], keyline[1]) }
          if keyline[1] in ssh_keys:
            existing_email_address = next(x for x in auth_users if auth_users[x]['sshkey'][1] == keyline[1])
            print("Error: The SSH key ...%s is present multiple times. sshd will just use the first one it encounters. "
                  "%s and %s at least are using the same key" % (keyline[1][-20:], keyline[2], existing_email_address))
          ssh_keys.add(keyline[1])
        else:
          if keyline[1] != auth_users[keyline[2]]['sshkey'][1]:
            print("Error: we've already seen a key for user %s and it doesn't match the key in %s" % (keyline[2], filename))
        if not 'ports' in auth_users[keyline[2]]:
          auth_users[keyline[2]]['ports'] = []
        auth_users[keyline[2]]['ports'].append(network_port)
        if not keyline[2] in user_instances:
          user_instances[keyline[2]] = []
        user_instances[keyline[2]].append(filename)
  except IOError:
    print("Couldn't find key file (" + keydir + filename + ") for instance " + filename + ". Skipping")

authkey_file = open(outdir+'authorized_keys', 'w')

print("Writing %s file" % authorized_keys_file)

for user_id,key in auth_users.items():
  open_ports = ','.join(map(PermitOpen, key['ports']))
  auth_line = auth_template + open_ports + ',command="exec /home/vps/bin/ganeti_cli.py %s"' % user_id
  authkey_file.write(' '.join([auth_line, key['sshkey'][0], key['sshkey'][1], user_id])+'\n')

attr_file = open(attributes_py_file, 'w')

print("Writing %s file" % attributes_py_file)

attr_file.write("""# ACL mapping permissions to domains for various users
from templates import *

ATTRIBUTES = {
  'unauthenticated_user': {
  },
""")

for user in user_instances:
  attr_file.write("  '%s': {\n" % user)
  for instance in user_instances[user]:
    attr_file.write("    '%s': TEMPLATES['standard'],\n" % instance)
  attr_file.write("  },\n")

attr_file.write("}\n")
attr_file.close()
