#
# RTEMS Tools Project (http://www.rtems.org/)
# Copyright 2010-2013 Chris Johns (chrisj@rtems.org)
# All rights reserved.
#
# This file is part of the RTEMS Tools package in 'rtems-tools'.
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

#
# This code builds a package given a config file. It only builds to be
# installed not to be package unless you run a packager around this.
#

import hashlib
import os
import stat
import sys
import urllib2
import urlparse

import cvs
import error
import git
import log
import path
import sources

def _humanize_bytes(bytes, precision = 1):
    abbrevs = (
        (1 << 50L, 'PB'),
        (1 << 40L, 'TB'),
        (1 << 30L, 'GB'),
        (1 << 20L, 'MB'),
        (1 << 10L, 'kB'),
        (1, ' bytes')
    )
    if bytes == 1:
        return '1 byte'
    for factor, suffix in abbrevs:
        if bytes >= factor:
            break
    return '%.*f%s' % (precision, float(bytes) / factor, suffix)

def _hash_check(file_, absfile, macros, remove = True):
    failed = False
    hash = sources.get_hash(file_.lower(), macros)
    if hash is not None:
        hash = hash.split()
        if len(hash) != 2:
            raise error.internal('invalid hash format: %s' % (file_))
        try:
            hashlib_algorithms = hashlib.algorithms
        except:
            hashlib_algorithms = ['md5', 'sha1', 'sha224', 'sha256', 'sha384', 'sha512']
        if hash[0] not in hashlib_algorithms:
            raise error.general('invalid hash algorithm for %s: %s' % (file_, hash[0]))
        hasher = None
        _in = None
        try:
            hasher = hashlib.new(hash[0])
            _in = open(absfile, 'rb')
            hasher.update(_in.read())
        except IOError, err:
            log.notice('hash: %s: read error: %s' % (file_, str(err)))
            failed = True
        except:
            msg = 'hash: %s: error' % (file_)
            log.stderr(msg)
            log.notice(msg)
            if _in is not None:
                _in.close()
            raise
        if _in is not None:
            _in.close()
        log.output('checksums: %s: %s => %s' % (file_, hasher.hexdigest(), hash[1]))
        if hasher.hexdigest() != hash[1]:
            log.warning('checksum error: %s' % (file_))
            failed = True
        if failed and remove:
            log.warning('removing: %s' % (file_))
            if path.exists(absfile):
                os.remove(path.host(absfile))
        if hasher is not None:
            del hasher
    else:
        log.warning('%s: no hash found' % (file_))
    return not failed

def _http_parser(source, config, opts):
    #
    # Is the file compressed ?
    #
    esl = source['ext'].split('.')
    if esl[-1:][0] == 'gz':
        source['compressed-type'] = 'gzip'
        source['compressed'] = '%{__gzip} -dc'
    elif esl[-1:][0] == 'bz2':
        source['compressed-type'] = 'bzip2'
        source['compressed'] = '%{__bzip2} -dc'
    elif esl[-1:][0] == 'zip':
        source['compressed-type'] = 'zip'
        source['compressed'] = '%{__zip} -u'
    elif esl[-1:][0] == 'xz':
        source['compressed-type'] = 'xz'
        source['compressed'] = '%{__xz} -dc'

def _patchworks_parser(source, config, opts):
    source['url'] = 'http%s' % (source['path'][2:])

def _git_parser(source, config, opts):
    #
    # Symlink.
    #
    us = source['url'].split('?')
    source['path'] = path.dirname(us[0])
    source['file'] = path.basename(us[0])
    source['name'], source['ext'] = path.splitext(source['file'])
    if len(us) > 1:
        source['args'] = us[1:]
    source['local'] = \
        path.join(source['local_prefix'], 'git', source['file'])
    source['symlink'] = source['local']

def _cvs_parser(source, config, opts):
    #
    # Symlink.
    #
    if not source['url'].startswith('cvs://'):
        raise error.general('invalid cvs path: %s' % (source['url']))
    us = source['url'].split('?')
    try:
        url = us[0]
        source['file'] = url[url[6:].index(':') + 7:]
        source['cvsroot'] = ':%s:' % (url[6:url[6:].index('/') + 6:])
    except:
        raise error.general('invalid cvs path: %s' % (source['url']))
    for a in us[1:]:
        _as = a.split('=')
        if _as[0] == 'module':
            if len(_as) != 2:
                raise error.general('invalid cvs module: %s' % (a))
            source['module'] = _as[1]
        elif _as[0] == 'src-prefix':
            if len(_as) != 2:
                raise error.general('invalid cvs src-prefix: %s' % (a))
            source['src_prefix'] = _as[1]
        elif _as[0] == 'tag':
            if len(_as) != 2:
                raise error.general('invalid cvs tag: %s' % (a))
            source['tag'] = _as[1]
        elif _as[0] == 'date':
            if len(_as) != 2:
                raise error.general('invalid cvs date: %s' % (a))
            source['date'] = _as[1]
    if 'date' in source and 'tag' in source:
        raise error.general('cvs URL cannot have a date and tag: %s' % (source['url']))
    # Do here to ensure an ordered path, the URL can include options in any order
    if 'module' in source:
        source['file'] += '_%s' % (source['module'])
    if 'tag' in source:
        source['file'] += '_%s' % (source['tag'])
    if 'date' in source:
        source['file'] += '_%s' % (source['date'])
    for c in '/@#%.-':
        source['file'] = source['file'].replace(c, '_')
    source['local'] = path.join(source['local_prefix'], 'cvs', source['file'])
    if 'src_prefix' in source:
        source['symlink'] = path.join(source['local'], source['src_prefix'])
    else:
        source['symlink'] = source['local']

def _file_parser(source, config, opts):
    #
    # Symlink.
    #
    source['symlink'] = source['local']

parsers = { 'http': _http_parser,
            'ftp':  _http_parser,
            'pw':   _patchworks_parser,
            'git':  _git_parser,
            'cvs':  _cvs_parser,
            'file': _file_parser }

def parse_url(url, pathkey, config, opts):
    #
    # Split the source up into the parts we need.
    #
    source = {}
    source['url'] = url
    colon = url.find(':')
    if url[colon + 1:colon + 3] != '//':
        raise error.general('malforned URL: %s' % (url))
    source['path'] = url[:colon + 3] + path.dirname(url[colon + 3:])
    source['file'] = path.basename(url)
    source['name'], source['ext'] = path.splitext(source['file'])
    if source['name'].endswith('.tar'):
        source['name'] = source['name'][:-4]
        source['ext'] = '.tar' + source['ext']
    #
    # Get the file. Checks the local source directory first.
    #
    source['local'] = None
    for p in config.define(pathkey).split(':'):
        local = path.join(path.abspath(p), source['file'])
        if source['local'] is None:
            source['local_prefix'] = path.abspath(p)
            source['local'] = local
        if path.exists(local):
            source['local_prefix'] = path.abspath(p)
            source['local'] = local
            _hash_check(source['file'], local, config.macros)
            break
    source['script'] = ''
    for p in parsers:
        if url.startswith(p):
            source['type'] = p
            if parsers[p](source, config, opts):
                break
    return source

def _http_downloader(url, local, config, opts):
    if path.exists(local):
        return True
    #
    # Hack for GitHub.
    #
    if url.startswith('https://api.github.com'):
        url = urlparse.urljoin(url, config.expand('tarball/%{version}'))
    dst = os.path.relpath(path.host(local))
    log.notice('download: %s -> %s' % (url, dst))
    failed = False
    if not opts.dry_run():
        _in = None
        _out = None
        _length = None
        _have = 0
        _chunk_size = 256 * 1024
        _chunk = None
        _last_percent = 200.0
        _last_msg = ''
        _wipe_output = False
        try:
            try:
                _in = urllib2.urlopen(url)
                _out = open(path.host(local), 'wb')
                try:
                    _length = int(_in.info().getheader('Content-Length').strip())
                except:
                    pass
                while True:
                    _msg = '\rdownloading: %s - %s ' % (dst, _humanize_bytes(_have))
                    if _length:
                        _percent = round((float(_have) / _length) * 100, 2)
                        if _percent != _last_percent:
                            _msg += 'of %s (%0.0f%%) ' % (_humanize_bytes(_length), _percent)
                    if _msg != _last_msg:
                        extras = (len(_last_msg) - len(_msg))
                        log.stdout_raw('%s%s' % (_msg, ' ' * extras + '\b' * extras))
                        _last_msg = _msg
                    _chunk = _in.read(_chunk_size)
                    if not _chunk:
                        break
                    _out.write(_chunk)
                    _have += len(_chunk)
                if _wipe_output:
                    log.stdout_raw('\r%s\r' % (' ' * len(_last_msg)))
                else:
                    log.stdout_raw('\n')
            except:
                log.stdout_raw('\n')
                raise
        except IOError, err:
            log.notice('download: %s: error: %s' % (url, str(err)))
            if path.exists(local):
                os.remove(path.host(local))
            failed = True
        except ValueError, err:
            log.notice('download: %s: error: %s' % (url, str(err)))
            if path.exists(local):
                os.remove(path.host(local))
            failed = True
        except:
            msg = 'download: %s: error' % (url)
            log.stderr(msg)
            log.notice(msg)
            if _out is not None:
                _out.close()
            raise
        if _out is not None:
            _out.close()
        if _in is not None:
            del _in
        if not failed:
            if not path.isfile(local):
                raise error.general('source is not a file: %s' % (path.host(local)))
            if not _hash_check(path.basename(local), local, config.macros, False):
                raise error.general('checksum failure file: %s' % (dst))
    return not failed

def _git_downloader(url, local, config, opts):
    rlp = os.path.relpath(path.host(local))
    us = url.split('?')
    repo = git.repo(local, opts, config.macros)
    if not repo.valid():
        log.notice('git: clone: %s -> %s' % (us[0], rlp))
        if not opts.dry_run():
            repo.clone(us[0], local)
    else:
        repo.clean(['-f', '-d'])
        repo.reset('--hard')
        repo.checkout('master')
    for a in us[1:]:
        _as = a.split('=')
        if _as[0] == 'branch' or _as[0] == 'checkout':
            if len(_as) != 2:
                raise error.general('invalid git branch/checkout: %s' % (_as))
            log.notice('git: checkout: %s => %s' % (us[0], _as[1]))
            if not opts.dry_run():
                repo.checkout(_as[1])
        elif _as[0] == 'pull':
            log.notice('git: pull: %s' % (us[0]))
            if not opts.dry_run():
                repo.pull()
        elif _as[0] == 'submodule':
            if len(_as) != 2:
                raise error.general('invalid git submodule: %s' % (_as))
            log.notice('git: submodule: %s <= %s' % (us[0], _as[1]))
            if not opts.dry_run():
                repo.submodule(_as[1])
        elif _as[0] == 'fetch':
            log.notice('git: fetch: %s -> %s' % (us[0], rlp))
            if not opts.dry_run():
                repo.fetch()
        elif _as[0] == 'reset':
            arg = []
            if len(_as) > 1:
                arg = ['--%s' % (_as[1])]
            log.notice('git: reset: %s' % (us[0]))
            if not opts.dry_run():
                repo.reset(arg)
    return True

def _cvs_downloader(url, local, config, opts):
    rlp = os.path.relpath(path.host(local))
    us = url.split('?')
    module = None
    tag = None
    date = None
    src_prefix = None
    for a in us[1:]:
        _as = a.split('=')
        if _as[0] == 'module':
            if len(_as) != 2:
                raise error.general('invalid cvs module: %s' % (a))
            module = _as[1]
        elif _as[0] == 'src-prefix':
            if len(_as) != 2:
                raise error.general('invalid cvs src-prefix: %s' % (a))
            src_prefix = _as[1]
        elif _as[0] == 'tag':
            if len(_as) != 2:
                raise error.general('invalid cvs tag: %s' % (a))
            tag = _as[1]
        elif _as[0] == 'date':
            if len(_as) != 2:
                raise error.general('invalid cvs date: %s' % (a))
            date = _as[1]
    repo = cvs.repo(local, opts, config.macros, src_prefix)
    if not repo.valid():
        if not path.isdir(local):
            log.notice('Creating source directory: %s' % \
                           (os.path.relpath(path.host(local))))
            if not opts.dry_run():
                path.mkdir(local)
            log.notice('cvs: checkout: %s -> %s' % (us[0], rlp))
            if not opts.dry_run():
                repo.checkout(':%s' % (us[0][6:]), module, tag, date)
    for a in us[1:]:
        _as = a.split('=')
        if _as[0] == 'update':
            log.notice('cvs: update: %s' % (us[0]))
            if not opts.dry_run():
                repo.update()
        elif _as[0] == 'reset':
            log.notice('cvs: reset: %s' % (us[0]))
            if not opts.dry_run():
                repo.reset()
    return True

def _file_downloader(url, local, config, opts):
    if path.exists(local):
        return True
    return path.isdir(url)

downloaders = { 'http': _http_downloader,
                'ftp':  _http_downloader,
                'pw':   _http_downloader,
                'git':  _git_downloader,
                'cvs':  _cvs_downloader,
                'file': _file_downloader }

def get_file(url, local, opts, config):
    if local is None:
        raise error.general('source/patch path invalid')
    if not path.isdir(path.dirname(local)) and not opts.download_disabled():
        log.notice('Creating source directory: %s' % \
                       (os.path.relpath(path.host(path.dirname(local)))))
    log.output('making dir: %s' % (path.host(path.dirname(local))))
    if not opts.dry_run():
        path.mkdir(path.dirname(local))
    if not path.exists(local) and opts.download_disabled():
        raise error.general('source not found: %s' % (path.host(local)))
    #
    # Check if a URL has been provided on the command line.
    #
    url_bases = opts.urls()
    urls = []
    if url_bases is not None:
        for base in url_bases:
            if base[-1:] != '/':
                base += '/'
            url_path = urlparse.urlsplit(url)[2]
            slash = url_path.rfind('/')
            if slash < 0:
                url_file = url_path
            else:
                url_file = url_path[slash + 1:]
            urls.append(urlparse.urljoin(base, url_file))
    urls += url.split()
    log.trace('_url: %s -> %s' % (','.join(urls), local))
    for url in urls:
        for dl in downloaders:
            if url.startswith(dl):
                if downloaders[dl](url, local, config, opts):
                    return
    if not opts.dry_run():
        raise error.general('downloading %s: all paths have failed, giving up' % (url))
