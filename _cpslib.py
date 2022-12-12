"""Copyright 2016 Google Inc. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.


Methods to interact with the Google Cloud Print Service APIs.

GCPService will provide methods to access all of Cloud Print's Interfaces:

delete (delete printer)
deletejob (delete print job)
jobs (get print jobs)
list (list printers belonging to a specific proxy)
printer (get printer capabilities and info)
register (register a printer)
search (search for printers and return basic information)
submit (submit a print job)

These interfaces are not used in the library, as they are printer specific:

control (used by printer to update job state)
fetch (used by printer to get the next print job)
update (used by printer to update printer attributes)
share (used by applications and interacts with GCP, not printers)
unshare (used by applications and only interacts with GCP, not printers)


This module is dependent on modules from the LogoCert package.
"""
from _common import Sleep
from _common import Extract
from _config import Constants
from _transport import Transport

from json import dumps
from os.path import basename
import requests
import mimetypes
import time
import _log

class GCPService(object):
  """Send and receive network messages and communication."""

  def __init__(self, auth_token):
    """Get a reference to a logger object.

      Args:
      auth_token: the authentication token to use for GCP requests
    """
    self.auth_token = auth_token
    self.logger = _log.GetLogger('LogoCert')
    self.transport = Transport(self.logger)


  def VerifyNotNone(query):
    """Decorator to check that None is not returned.
       This keeps calling code cleaner

    Args:
      query: function we are wrapping.
    Returns:
      formatted data from query if valid, otherwise raise exception
    """
    def VerifyNotNone(self, *args, **kwargs):
      res = query(self, *args, **kwargs)
      if res is None:
        print '%s failed' % str(query)
        raise AssertionError
      return res

    return VerifyNotNone


  def HTTPGetQuery(query):
    """Decorator for various queries to GCP interfaces

    Args:
      query: function we are wrapping.
    Returns:
      formatted data from query to GCP Service Interface.
    """
    def GCPQuery(self, *args, **kwargs):
      url = query(self, *args, **kwargs)
      headers = {'Authorization': 'Bearer %s' % self.auth_token}
      res = self.transport.HTTPGet(url, headers=headers)

      response_dict = {}
      Extract(res.json(), response_dict)
      return response_dict

    return GCPQuery

  @VerifyNotNone
  def FetchRaster(self, job_id):
    """Get the data content belonging to a job_id in pwg-raster format
       Note: This only works for job_id's that are queued, or in_progress.
             This will not work for jobs that have finished

       Args:
          job_id: string, printer's id
       Returns:
         str, the content in pwg-raster format if successful, otherwise, None

         """
    url = '%s/download?id=%s&forcepwg=1' % (Constants.GCP['MGT'], job_id)
    headers = {'Authorization': 'Bearer %s' % self.auth_token}
    r = self.transport.HTTPGet(url, headers=headers)

    if r is None or requests.codes.ok != r.status_code:
      if r is None:
        print 'ERROR! Request to /download returned None type'
      elif r.status_code == 415:
        print ('GCP failed to provide raster file conversion, either supply '
               'your own raster files or capture the content via wireshark '
               'from manual printing the following from Chrome: testpage.png, '
               'rosemary.pdf, dna_overview.png')
      else:
        print ('ERROR! Bad HTTP status code received from /download: %s' %
               r.status_code)
      return None

    return r.content


  # Not decorated with @HTTPGetQuery since Submit() is an HTTP Post
  @VerifyNotNone
  def Submit(self, printer_id, content, title, cjt=None, is_url=False):
    """Submit a print job to the printer

        Args:
          printer_id: string, target printer to print from.
          content: string, url or absolute filepath of the item to print.
          title: string, title of the print job.
          cjt: CloudJobTicket, object that defines the options of the print job
          is_url: boolean, flag to identify between url's and files
        Returns:
          dictionary, response msg from the printer if successful;
                      otherwise, None
        """

    if cjt is None:
      cjt = {}
    else:
      cjt = cjt.val

    name = content

    if not is_url:
      name = basename(content)
      with open(content, 'rb') as f:
        content = f.read()

    if title is None:
      title = "LogoCert Testing: " + name

    content_type = 'url' if is_url else mimetypes.guess_type(name)[0]
    files = {"content": (name,content)}
    url = '%s/submit' % (Constants.GCP['MGT'])
    headers = {'Authorization': 'Bearer %s' % self.auth_token}

    data = {'printerid': printer_id,
            'title': title,
            'contentType': content_type,
            'ticket': dumps(cjt)}

    # Depending on network speed, large files may take a while to submit
    print 'Attempting to submit a job through GCP for up to 60 seconds'
    t_end = time.time()+60
    while time.time() < t_end:
      r = self.transport.HTTPPost(url, data=data, files=files, headers=headers)
      if r is None:
        print 'ERROR! HTTP POST to /submit returned None type'
        return None
      elif r.status_code == requests.codes.ok:
        # Success
        res = r.json()
        # TODO may have to fuzzy match here, print job added may not be standard
        res['success'] = (res['success'] and
                          'print job added' in res['message'].lower())

        if res['success']:
          print 'Job submitted successfully'
        else:
          print 'success: %s, msg: %s' % (res['success'], res['message'])
        return res
      else:
        # Try again if we get HTTP error code
        print 'Bad status code from Submit(): %s' % r.status_code
        if r.status_code == requests.codes.forbidden:
          # This should not happen, calling code should manage token refresh
          self.logger.info('Access token expired, need to refresh it.')
        print 'Trying again in %s sec(s)' % Constants.SLEEP['POLL']
        Sleep('POLL')
    # Continuously gotten HTTP error codes to fall out of the while loop
    return None

  # Not decorated with @HTTPGetQuery since Update() is an HTTP Post
  @VerifyNotNone
  def Update(self, printer_id, setting):
    """Update a cloud printer

        Args:
          printer_id: string, target printer to update.
          setting: dict, local settings structure that describes the fields
                         to update
        Returns:
          dictionary, response msg from the printer
        """
    url = '%s/update' % (Constants.GCP['MGT'])
    headers = {'Authorization': 'Bearer %s' % self.auth_token}

    data = {'printerid': printer_id,
            'local_settings': dumps(setting)}

    r = self.transport.HTTPPost(url, data=data, headers=headers)

    if r is None or requests.codes.ok != r.status_code:
      return False

    res = r.json()
    # TODO may have to fuzzy match here, print job added may not be a standard
    res['success'] = (res['success'] and
                      'printer updated successfully' in res['message'].lower())
    return res


  @VerifyNotNone
  @HTTPGetQuery
  def Delete(self, printer_id):
    """Delete a printer owned by a user.

    Args:
      printer_id: string, printerid of registered printer.
    Returns:
      url: string, url to delete printer.
    """
    url = '%s/delete?printerid=%s' % (Constants.GCP['MGT'], printer_id)

    return url

  @VerifyNotNone
  @HTTPGetQuery
  def DeleteJob(self, job_id):
    """Delete a job owned by user.

    Args:
      job_id: string, jobid of existing job owned by user.
    Returns:
      url: string, url to delete job.
    """
    url = '%s/deletejob?jobid=%s' % (Constants.GCP['MGT'], job_id)

    return url

  @VerifyNotNone
  @HTTPGetQuery
  def Jobs(self, printer_id=None, owner=None, job_title=None, status=None):
    """Get a list of print jobs which user has permission to view.

    Args:
      printer_id: string, filter jobs sent to this printer.
      owner: string, filter jobs submitted by this owner.
      job_title: string, filter jobs whose title or tags contain this string.
      status: string, filter jobs that match this status.
    Returns:
      string, url to be used by HTTPGetQuery method.
    Valid Job state strings are: QUEUED, IN_PROGRESS, DONE, ERROR, SUBMITTED,
    and HELD.
    """
    args = '?'
    url = '%s/jobs' % Constants.GCP['MGT']
    if printer_id:
      url += '?printerid=%s' % printer_id
      args = '&'
    if owner:
      url += '%sowner=%s' % (args, owner)
      args = '&'
    if status:
      url += '%sstatus=%s' % (args, status)
      args= '&'
    if job_title:
      url += '%sq=%s' % (args, job_title)

    return url

  @VerifyNotNone
  @HTTPGetQuery
  def List(self, proxy_id):
    """Execute the list interface and return printer fields.

    Args:
      proxy_id: string, proxy of printer.
    Returns:
      string: url to by used by HTTPGetQuery method.
    Note: the List interface returns the same information as the Search
    interface; therefore, use the Search interface unless you need a list
    or printers using the same proxy_id.
    """
    url = '%s/list?proxy=%s' % (Constants.GCP['MGT'], proxy_id)

    return url

  @VerifyNotNone
  @HTTPGetQuery
  def Printer(self, printer_id):
    """Execute the printer interface and return printer fields and capabilites.

    Args:
      printer_id: string, id of printer.
    Returns:
      string: url to be used by HTTPGetQuery method.
    """
    fields = 'connectionStatus,semanticState,uiState,queuedJobsCount'
    url = '%s/printer?printerid=%s&usecdd=True&extra_fields=%s' % (
        Constants.GCP['MGT'], printer_id, fields)

    return url

  @VerifyNotNone
  @HTTPGetQuery
  def Search(self, printer=None):
    """Search for printers owned by user.

    Args:
      printer: string, name or partial name of printer to search for.
    Returns:
      string: url to be used by HTTPGetQuery method.
    """
    url = '%s/search' % Constants.GCP['MGT']
    if printer:
      # replace all spaces with %20
      url += '?q=%s' % printer.replace(' ','%20')

    return url


  def __getJobFromList(self, job_list, job_id):
    """Find the specified job_id in a list of jobs

    Args:
      job_list: array, job objects.
      job_id: the job_id to look for
    Returns:
      object: the job object with the specified job_id
    """
    for entry in job_list:
      if entry['id'] == job_id:
        return entry
    return None

  def GetJobInfo(self, job_id, printer_id, owner=None, job_title=None):
      """Find the specified job_id in from the Job query result

          Args:
            job_id: string, id of the print job.
            printer_id: string, id of the printer
            owner: string, filter jobs submitted by this owner.
            job_title: string, filter jobs whose title or tags contain this str.
          Returns:
            object: the job object with the specified job_id
      """
      res = self.Jobs(printer_id=printer_id, owner=owner, job_title=job_title)
      job = self.__getJobFromList(res['jobs'], job_id)
      return job

  @VerifyNotNone
  def WaitJobStateNotIn(self, job_id, printer_id, job_state, timeout=60):
    """Wait until the job state is not the specified state.

    Args:
      job_id: string, id of the print job.
      printer_id: string, id of the printer
      job_state: string or list, job state(s) that should not be observed.
      timeout: integer, number of seconds to wait.
    Returns:
      string, current job.

    """
    print ('Waiting up to %s seconds for the job to not have any of the '
           'following job state(s): %s\n' % (timeout, job_state))

    end = time.time() + timeout

    while time.time() < end:
      job = self.GetJobInfo(job_id, printer_id)

      if job is not None:
        if job['semanticState']['state']['type'] not in job_state:
          return job

      Sleep('POLL')

    return None

  @VerifyNotNone
  def WaitJobStateIn(self, job_id, printer_id, job_state, timeout=60):
    """Wait until the job state becomes the specified state(s)

    Args:
      job_id: string, id of the print job.
      printer_id: string, id of the printer
      job_state: string or list, job state(s) to wait for.
      timeout: integer, number of seconds to wait.
    Returns:
      dict, current job.

    """
    print ('Waiting up to %s seconds for the job to have one of the following '
           'job state(s): %s\n' % (timeout, job_state))

    end = time.time() + timeout

    while time.time() < end:
      job = self.GetJobInfo(job_id, printer_id)

      if job is not None:
        if job['semanticState']['state']['type'] in job_state:
          return job

      Sleep('POLL')

    return None


  def WaitForUpdate(self, dev_id, key, expected_value,
                    timeout=Constants.TIMEOUT['GCP_UPDATE']):
    '''Wait for the printer's local_settings attribute matches an expected value

      Args:
        dev_id: string, id of the printer.
        key: string, the local_settings attribute to poll for.
        expected_value: int or boolean, the expected value of the attribute.
        timeout: integer, number of seconds to wait.
      Returns:
        boolean, True if expected value is observed, otherwise False
    '''
    print '[Configurable timeout] GCP_UPDATE:'
    print ('Waiting up to %s seconds for printer to accept pending settings' %
           timeout)

    end = time.time() + timeout

    while time.time() < end:
      # Continue to use the /Update to access the current local settings
      try:
        res = self.Update(dev_id,{})
      except AssertionError:
        print 'GCP Update call failed'
        return False
      else:
        if key not in res['printer']['local_settings']['current']:
          print 'ERROR: "%s" does not exist in local_settings' % key
          return False
        cur_val = res['printer']['local_settings']['current'][key]
        if expected_value == cur_val:
          return True
      Sleep('POLL')
    return False


  def WaitLocalJobExist(self, printer_id, job_title, timeout=60):
    """Wait until the local print job is present in /job api.

    Args:
      printer_id: string, id of the printer
      job_title: string, title of the print job.
      timeout: integer, number of seconds to wait.
    Returns:
      boolean, True if job exists.

    """
    print ('Waiting up to %s seconds for the local print job to be reported to '
           'GCP servers\n' % timeout)

    end = time.time() + timeout

    while time.time() < end:
      res = self.Jobs(printer_id=printer_id, job_title=job_title)
      if res['jobsCount'] > 0:
        return True

      Sleep('POLL')

    return False
