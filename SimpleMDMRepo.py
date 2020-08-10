# encoding: utf-8

# SimpleMDMRepo.py
# Version 1.1.1

from __future__ import absolute_import, print_function

import base64
import getpass
import json
import os
import subprocess
import tempfile
import re
import sys

try:
    from urllib2 import quote
except ImportError:
    from urllib.parse import quote

from munkilib.munkirepo import Repo, RepoError
    
DEFAULT_BASE_URL = 'https://a.simplemdm.com/munki/plugin'

class SimpleMDMRepo(Repo):

    def __init__(self, baseurl):
        self.base_url = DEFAULT_BASE_URL
        if 'SIMPLEMDM_BASE_URL' in os.environ:
            self.base_url = os.environ['SIMPLEMDM_BASE_URL']

        self.getter      = URLGetter()
        self.auth_header = self._fetch_auth_header()
        print(f'NOTICE: The SimpleMDMRepo plugin ignores the MUNKI_REPO value. "{self.base_url}" will be used instead.')
        
    def _fetch_api_key(self):
        if 'SIMPLEMDM_API_KEY' in os.environ:
            return os.environ['SIMPLEMDM_API_KEY']
        else:
            print(f'Please provide a SimpleMDM API key')
            return getpass.getpass()

    def _fetch_auth_header(self):
        key          = self._fetch_api_key()
        token_bytes  = f'{key}:'.encode("UTF-8")
        base64_bytes = base64.b64encode(token_bytes)
        base64_str   = base64_bytes.decode("UTF-8")
        header       = f'Basic {base64_str}'

        return header

    def _curl(self, simplemdm_path_or_url, commands=None, form_data=None, headers=None, simplemdm_request=True):
        if not commands:
            commands = []
        if not form_data:
            form_data = {}
        if not headers:
            headers = {}

        curl_cmd = self.getter.prepare_curl_cmd()

        # headers

        if simplemdm_request:
            headers['Authorization'] = self.auth_header

        self.getter.add_curl_headers(curl_cmd, headers)

        # form data

        for key, value in form_data.items():
            curl_cmd.extend(['-F', f'{key}={value}'])

        # commands
        
        curl_cmd.extend(commands)
        curl_cmd.append('-v')

        # url

        if simplemdm_request:
            url = os.path.join(self.base_url, quote(simplemdm_path_or_url.encode('UTF-8')))
        else:
            url = simplemdm_path_or_url

        curl_cmd.append(url)

        resp = self.getter.download_with_curl(curl_cmd, False)

        return resp

    def itemlist(self, kind):
        resp = self._curl(kind)
        return json.loads(resp)

    def get(self, resource_identifier):
        resp = self._curl(resource_identifier)
        return resp

    def put(self, resource_identifier, content):
        commands = ['-X', 'POST']

        if len(content) > 1024:
            fileref, contentpath = tempfile.mkstemp()
            fileobj = os.fdopen(fileref, 'wb')
            fileobj.write(content)
            fileobj.close()
            commands.extend(['-T', contentpath])
        else:
            commands.extend(['-d', content])

        self._curl(resource_identifier, commands=commands)

        if contentpath:
            os.unlink(contentpath)
   
    def put_from_local_file(self, resource_identifier, local_file_path):
        if(resource_identifier.startswith('pkgs/')):
            filename = resource_identifier[len('pkgs/'):]

            # fetch upload url

            form_data = {
                'filename': filename
            }
            resp = self._curl('pkgs/create_url', form_data=form_data)
            upload_url = resp.decode("UTF-8")
            
            # upload binary

            headers = { 'Content-type': 'application/octet-stream' }
            commands = ['-X', 'PUT', '-T', local_file_path]
            self._curl(upload_url, commands=commands, headers=headers, simplemdm_request=False)

            # upload callback

            form_data = {
                'filename': filename,
                'upload_url': upload_url
            }
            self._curl('pkgs/create_callback', form_data=form_data)
        else:
            headers = { 'Content-type': 'application/octet-stream' }
            commands = ['-X', 'POST', '-T', local_file_path]
            self._curl(resource_identifier, headers=headers, commands=commands)

    def delete(self, resource_identifier):
        raise ProcessorError("This action is not supported by SimpleMDM") 

# Borrowed/adapted from AutoPkg, to eliminate AutoPkg lib dependency

def log_err(msg):
    """Message logger for errors."""
    log(msg, error=True)

def is_executable(exe_path):
    """Is exe_path executable?"""
    return os.path.exists(exe_path) and os.access(exe_path, os.X_OK)

class ProcessorError(Exception):
    """Base Error class"""

    pass

class Processor:
    """Processor base class.

    Processors accept a property list as input, process its contents, and
    returns a new or updated property list that can be processed further.
    """

    def __init__(self, env=None, infile=None, outfile=None):
        # super(Processor, self).__init__()
        self.env = env
        if infile is None:
            self.infile = sys.stdin
        else:
            self.infile = infile
        if outfile is None:
            self.outfile = sys.stdout
        else:
            self.outfile = outfile

    def output(self, msg, verbose_level=1):
        """Print a message if verbosity is >= verbose_level"""
        if int(self.env.get("verbose", 0)) >= verbose_level:
            print(f"{self.__class__.__name__}: {msg}")

    def main(self):
        """Stub method"""
        raise ProcessorError("Abstract method main() not implemented.")

    def get_manifest(self):
        """Return Processor's description, input and output variables"""
        try:
            return (self.description, self.input_variables, self.output_variables)
        except AttributeError as err:
            raise ProcessorError(f"Missing manifest: {err}")

    def read_input_plist(self):
        """Read environment from input plist."""

        try:
            indata = self.infile.buffer.read()
            if indata:
                self.env = plistlib.loads(indata)
            else:
                self.env = {}
        except BaseException as err:
            raise ProcessorError(err)

    def write_output_plist(self):
        """Write environment to output as plist."""

        if self.env is None:
            return

        try:
            with open(self.outfile, "wb") as f:
                plistlib.dump(self.env, f)
        except TypeError:
            plistlib.dump(self.env, self.outfile.buffer)
        except BaseException as err:
            raise ProcessorError(err)

    def parse_arguments(self):
        """Parse arguments as key='value'."""

        for arg in sys.argv[1:]:
            (key, sep, value) = arg.partition("=")
            if sep != "=":
                raise ProcessorError(f"Illegal argument '{arg}'")
            update_data(self.env, key, value)

    def inject(self, arguments):
        """Update environment data with arguments."""
        for key, value in list(arguments.items()):
            update_data(self.env, key, value)

    def process(self):
        """Main processing loop."""
        # Make sure all required arguments have been supplied.
        for variable, flags in list(self.input_variables.items()):
            # Apply default values to unspecified input variables
            if "default" in list(flags.keys()) and (variable not in self.env):
                self.env[variable] = flags["default"]
                self.output(
                    f"No value supplied for {variable}, setting default value "
                    f"of: {self.env[variable]}",
                    verbose_level=2,
                )
            # Make sure all required arguments have been supplied.
            if flags.get("required") and (variable not in self.env):
                raise ProcessorError(f"{self.__class__.__name__} requires {variable}")

        self.main()
        return self.env

    def cmdexec(self, command, description):
        """Execute a command and return output."""

        try:
            proc = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            (stdout, stderr) = proc.communicate()
        except OSError as err:
            raise ProcessorError(
                f"{command[0]} execution failed with error code "
                f"{err.errno}: {err.strerror}"
            )
        if proc.returncode != 0:
            raise ProcessorError(f"{description} failed: {stderr}")

        return stdout

    def execute_shell(self):
        """Execute as a standalone binary on the commandline."""

        try:
            self.read_input_plist()
            self.parse_arguments()
            self.process()
            self.write_output_plist()
        except ProcessorError as err:
            log_err(f"ProcessorError: {err}")
            sys.exit(10)
        else:
            sys.exit(0)

class URLGetter(Processor):
    """Handles curl HTTP operations. Serves only as superclass. Not for direct use."""

    description = __doc__

    def __init__(self, env=None, infile=None, outfile=None):
        super().__init__(env, infile, outfile)
        if not self.env:
            self.env = {}

    def curl_binary(self):
        """Return a path to a curl binary, priority in the order below.
        Return None if none found.
        1. env['CURL_PATH']
        2. a 'curl' binary that can be found in the PATH environment variable
        3. '/usr/bin/curl'
        """

        if "CURL_PATH" in self.env and is_executable(self.env["CURL_PATH"]):
            return self.env["CURL_PATH"]

        for path_env in os.environ["PATH"].split(":"):
            curlbin = os.path.join(path_env, "curl")
            if is_executable(curlbin):
                return curlbin

        if is_executable("/usr/bin/curl"):
            return "/usr/bin/curl"

        raise ProcessorError("Unable to locate or execute any curl binary")

    def prepare_curl_cmd(self):
        """Assemble basic curl command and return it."""
        return [self.curl_binary(), "--compressed", "--location"]

    def add_curl_headers(self, curl_cmd, headers):
        """Add headers to curl_cmd."""
        if headers:
            for header, value in headers.items():
                curl_cmd.extend(["--header", f"{header}: {value}"])

    def add_curl_common_opts(self, curl_cmd):
        """Add request_headers and curl_opts to curl_cmd."""
        self.add_curl_headers(curl_cmd, self.env.get("request_headers"))

        for item in self.env.get("curl_opts", []):
            curl_cmd.extend([item])

    def produce_etag_headers(self, filename):
        """Produce a dict of curl headers containing etag headers from the download."""
        headers = {}
        # If the download file already exists, add some headers to the request
        # so we don't retrieve the content if it hasn't changed
        if os.path.exists(filename):
            self.existing_file_size = os.path.getsize(filename)
            etag = self.getxattr(self.xattr_etag)
            last_modified = self.getxattr(self.xattr_last_modified)
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified
        return headers

    def clear_header(self, header):
        """Clear header dictionary."""
        # Save redirect URL before clear
        http_redirected = header.get("http_redirected", None)
        header.clear()
        header["http_result_code"] = "000"
        header["http_result_description"] = ""
        # Restore redirect URL
        header["http_redirected"] = http_redirected

    def parse_http_protocol(self, line, header):
        """Parse first HTTP header line."""
        try:
            header["http_result_code"] = line.split(None, 2)[1]
            header["http_result_description"] = line.split(None, 2)[2]
        except IndexError:
            pass

    def parse_http_header(self, line, header):
        """Parse single HTTP header line."""
        part = line.split(None, 1)
        fieldname = part[0].rstrip(":").lower()
        try:
            header[fieldname] = part[1]
        except IndexError:
            header[fieldname] = ""

    def parse_curl_error(self, proc_stderr):
        """Report curl failure."""
        curl_err = ""
        try:
            curl_err = proc_stderr.rstrip("\n")
            curl_err = curl_err.split(None, 2)[2]
        except IndexError:
            pass

        return curl_err

    def parse_ftp_header(self, line, header):
        """Parse single FTP header line."""
        part = line.split(None, 1)
        responsecode = part[0]
        if responsecode == "213":
            # This is the reply to curl's SIZE command on the file
            # We can map it to the HTTP content-length header
            try:
                header["content-length"] = part[1]
            except IndexError:
                pass
        elif responsecode.startswith("55"):
            header["http_result_code"] = "404"
            header["http_result_description"] = line
        elif responsecode == "150" or responsecode == "125":
            header["http_result_code"] = "200"
            header["http_result_description"] = line

    def parse_headers(self, raw_headers):
        """Parse headers from curl."""
        header = {}
        self.clear_header(header)
        for line in raw_headers.splitlines():
            if line.startswith("HTTP/"):
                self.parse_http_protocol(line, header)
            elif ": " in line:
                self.parse_http_header(line, header)
            elif self.env.get("url", "").startswith("ftp://"):
                self.parse_ftp_header(line, header)
            elif line == "":
                # we got an empty line; end of headers (or curl exited)
                if header.get("http_result_code") in [
                    "301",
                    "302",
                    "303",
                    "307",
                    "308",
                ]:
                    # redirect, so more headers are coming.
                    # Throw away the headers we've received so far
                    header["http_redirected"] = header.get("location", None)
                    self.clear_header(header)
        return header

    def execute_curl(self, curl_cmd, text=True):
        """Execute curl command. Return stdout, stderr and return code."""
        errors = "ignore" if text else None
        try:
            result = subprocess.run(
                curl_cmd,
                shell=False,
                bufsize=1,
                capture_output=True,
                check=True,
                text=text,
                errors=errors,
            )
        except subprocess.CalledProcessError as e:
            raise ProcessorError(e)
        return result.stdout, result.stderr, result.returncode

    def download_with_curl(self, curl_cmd, text=True):
        """Launch curl, return its output, and handle failures."""
        proc_stdout, proc_stderr, retcode = self.execute_curl(curl_cmd, text)
        self.output(f"Curl command: {curl_cmd}", verbose_level=4)
        if retcode:  # Non-zero exit code from curl => problem with download
            curl_err = self.parse_curl_error(proc_stderr)
            raise ProcessorError(f"curl failure: {curl_err} (exit code {retcode})")

        m = re.search('< HTTP/[0-9\\.]+[\s]+([0-9]+)', proc_stderr.decode('UTF-8'))
        status_code = m.group(1)
        if not re.match(r'\A(1|2|3)', status_code):
            raise ProcessorError(f"ERROR: Server returned code {status_code}: {proc_stdout.decode('UTF-8')}")

        return proc_stdout

    def download(self, url, headers=None, text=False):
        """Download content with default curl options."""
        curl_cmd = self.prepare_curl_cmd()
        self.add_curl_headers(curl_cmd, headers)
        curl_cmd.append(url)
        output = self.download_with_curl(curl_cmd, text)
        return output

    def download_to_file(self, url, filename, headers=None):
        """Download content to a file with default curl options."""
        curl_cmd = self.prepare_curl_cmd()
        self.add_curl_headers(curl_cmd, headers)
        curl_cmd.append(url)
        curl_cmd.extend(["-o", filename])
        self.download_with_curl(curl_cmd, text=False)
        if os.path.exists(filename):
            return filename
        raise ProcessorError(f"{filename} was not written!")

    def main(self):
        pass
