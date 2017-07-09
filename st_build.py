#!/usr/bin/env python
# This script handles the operations necessary to build and package webrtc for use in
# the sw-dev repo.
# - It aggregates the generated libraries into a single one (simplifies linking on the cmake side)
# - It generates a tar.gz that can be uploaded to repo.suitabletech.com
#
# Usage example:
# st_build.py --platform=linux-x64 --source_dir=~/webrtc-checkout -c Release --version=20170131_ac61b745df8eb918e8a39368fec7d7c3a890f221
#
# By convention, use the date and webrtc source revision for <version>. The date is convenient to quickly know how old the
# revision is, and webrtc rev number provides the exact reference for the webrtc source code. Note that you want to use the exact
# same version string for all platforms.
#
# Important note: the source directory contains platform-specific differences, so it is not possible to
# share the same source directory between different platforms (e.g. residing on a host OS and shared to VMs).

import optparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile

current_dir = os.getcwd()
script_dir = os.path.dirname(os.path.abspath(__file__))
source_dir = os.path.abspath(os.path.join(script_dir, os.pardir))

windows = platform.system() == 'Windows'
linux = platform.system() == 'Linux'
mac = platform.system() == 'Darwin'

class WebRTCPackager:
  # source_root: the source dir of webrtc, containing the src directory
  # build_root: the build directory of webrtc, containing a subdirectory for each configuration; defaults to the current working directory
  # version: the version is we use in download_thirdparty_binaries
  # platform: the platform we are packaging for
  # config: the webrtc config we are building for (Debug/Release or more depending on platform)
  def __init__(self, source_root, build_root, version, platform, config):
    self.source_root = source_root
    self.build_root = build_root
    self.version = version
    self.platform = platform
    self.config = config
    self.merged_static_library = None

  def getLibraryName(self, name):
    names = { 'linux-x64': 'lib' + name + '.so',
              'win32': name + '.lib',
              'osx': 'lib' + name + '.dylib',
              'linux-android-armeabi-v7a': 'lib' + name + '.so',
            }

    return names[self.platform]

  # Remove previous package directory
  def removePackageDir(self, package_dir):
    try:
      shutil.rmtree(package_dir)
    except OSError:
      pass

  # Copy the generated libaries and symbols to the package directory
  def buildPackageDirLibs(self, configuration, package_dir, lib_subdir):
    out_dir = os.path.join(self.build_root, configuration)

    if self.platform in [ 'linux-x64', 'linux-android-armeabi-v7a' ]:
      lib_dir = os.path.join(package_dir, lib_subdir)
      libs = findAllFilesWithExtension(out_dir, [ '.o' ])
      libs = [l for l in libs if l.find("example") < 0]
      # Create a single library to contain all the other ones. This has the added
      # benefit of making a full library instead of the thin libraries generated by gyp.
      # (it is also easier to link since we don't have to deal with the ordering)
      self.merged_static_library = "webrtc_all.a"
      linuxMergeLibraries(libs, out_dir, os.path.join(lib_dir, self.merged_static_library));

      libs = findAllFilesWithExtension(out_dir, [ '.so' ])
      copyFiles(out_dir, lib_dir, libs, False)
    elif self.platform in [ 'osx' ]:
      lib_dir = os.path.join(package_dir, lib_subdir)
      libs = findAllFilesWithExtension(out_dir, [ '.o' ])
      libs = [l for l in libs if l.find("example") < 0]
      self.merged_static_library = "webrtc_all.a"
      osxMergeLibraries(libs, out_dir, os.path.join(lib_dir, self.merged_static_library));

      libs = findAllFilesWithExtension(out_dir, [ '.dylib' ])
      copyFiles(out_dir, lib_dir, libs, False)
    elif self.platform in ['win32']:
      lib_dir = os.path.join(package_dir, lib_subdir)
      libs = findAllFilesWithExtension(out_dir, [ '.lib' ])
      self.merged_static_library = "webrtc_all.lib"
      winMergeLibraries(libs, out_dir, os.path.join(lib_dir, self.merged_static_library));

      # We want .dll but also .dll.lib .dll.pdb etc...
      libs = findAllFilesWithExtension(out_dir, [ re.compile('.*\.dll.*'), '.pdb' ])
      copyFiles(out_dir, lib_dir, libs, False)
    else:
      libs = findAllFilesWithExtension(out_dir, [ '.a', '.so', '.lib', '.dll' ])
      lib_dir = os.path.join(package_dir, lib_subdir)
      copyFiles(out_dir, lib_dir, libs)

  # Gather other files to the package directory
  def buildPackageDirSupport(self, package_dir):
    for subdir in [ 'webrtc', 'third_party' ]:
      # Gather all the header files from webrtc
      headers_ext = [ '.h', '.hpp', '.h.def' ]
      src = os.path.join(self.source_root, 'src', subdir)
      headers = findAllFilesWithExtension(src, headers_ext)
      copyFiles(src, os.path.join(package_dir, 'include', subdir), headers)

      # Copy license files
      license_ext = [ 'LICENSE', 'COPYING', 'LICENSE_THIRD_PARTY', 'PATENTS' ]
      license = findAllFilesWithExtension(src, license_ext)
      copyFiles(src, os.path.join(package_dir, 'licenses', subdir), license)

  # Make tar.gz archive
  def makePackageArchive(self, package_dir, version_name):
    os.chdir(self.build_root)
    archive_name = 'webrtc-' + version_name + '-' + self.platform + ".tar.gz"
    if subprocess.call([ 'cmake', '-E', 'tar', 'cvzf', archive_name, version_name]) != 0:
      sys.exit(1)

  # Build a tar.gz that we can upload to repo
  def buildPackage(self):
    if self.config in ['Release', 'Debug']:
      package_dir = os.path.join(self.build_root, self.version + '-' + self.config)
      version_name = self.version + '-' + self.config
      self.removePackageDir(package_dir)
      self.buildPackageDirLibs(self.config, package_dir, 'lib')
    else:
      package_dir = os.path.join(self.build_root, self.version)
      version_name = self.version
      self.removePackageDir(package_dir)
      self.buildPackageDirLibs('Release', package_dir, 'lib')
      self.buildPackageDirLibs('Debug', package_dir, 'debug_lib')

    self.buildPackageDirSupport(package_dir)
    self.makePackageArchive(package_dir, version_name)

  # Add to 'used' all the defines that appear to be used in source code in the given directory
  def filterDefines(self, dir, tofind, used):
    for root, dirs, files in os.walk(dir):
      for filename in files:
        try:
          content = open(os.path.join(root, filename)).read()
        except:
          continue
        found = []
        for d in tofind:
          if content.find(d) >= 0:
            used[d] = tofind[d]
            found.append(d)
        for d in found:
          del tofind[d]

  def extractLibsFromNinjaFile(self):
    fname = os.path.join('obj', 'webrtc', 'examples', 'peerconnection_client.ninja')
    if self.platform == "osx":
      fname = os.path.join('obj', 'webrtc', 'webrtc_common.ninja')

    if self.config in ['Release', 'Debug']:
      print "\nRetreiving build settings configuration for " + self.config
      fname = os.path.join(self.build_root, self.config, fname)
    else:
      print "\nRetreiving build settings configuration for Release"
      fname = os.path.join(self.build_root, 'Release', fname)

    extensions = [ '.lib', '.dll', '.a', '.so' ]
    lines = open(fname, 'r').read().split('\n')
    # Merge all the lines ending in $
    aggregated_lines = []
    acc = ""
    merge_next = False
    for l in lines:
      l1 = l
      if l.endswith('$'):
        l1 = l[:-1]
      if merge_next:
        acc += l1
      else:
        aggregated_lines.append(acc)
        acc = l1
      merge_next = l.endswith('$')
    #print "\n".join(aggregated_lines)

    # Extract libs. We just look for any file name with library extension
    # to capture files that could be in ldflags as well.
    libs = {}
    for l in lines:
      parts = l.split(' ')
      for p in parts:
        for ext in extensions:
          if p.endswith(ext):
            libs[p] = 1
            break

    # Extract defines
    defs = {}
    for l in aggregated_lines:
      m = re.match('\s*defines\s*=\s*(.*)', l)
      if m:
        for f in m.group(1).split(' '):
          if not re.match('^\s*$', f):
            defs[f] = 1

    print "set(webrtc_LIBS"
    if self.merged_static_library:
      print "  " + self.merged_static_library
    else:
      print "\n".join(["  " + x for x in libs.keys()])
    print ")"

    used_defs = {}
    # Remove the -D and =xxxx from the define to get the name
    def_names = { re.sub('-D(\w+).*', '\\1', d):d for d in defs }
    # Look which defines are actually used in webrtc and only print those.
    self.filterDefines(os.path.join(self.source_root, 'src', 'third_party'), def_names, used_defs)
    self.filterDefines(os.path.join(self.source_root, 'src', 'webrtc'), def_names, used_defs)

    print "set(webrtc_DEFS"
    print "\n".join(["  " + used_defs[x] for x in used_defs.keys()])
    print ")"

    print "Unused defs:", defs

def safeMakeDirs(dir):
  try:
    os.makedirs(dir)
  except OSError:
    pass

def linuxMergeLibraries(libs, src_dir, destination):
  safeMakeDirs(os.path.dirname(destination))
  p = subprocess.Popen(['ar','-M'],stdin=subprocess.PIPE)
  p.stdin.write('create %s\n' % destination)
  for l in libs:
    p.stdin.write('addmod %s\n' % os.path.join(src_dir, l))
  p.stdin.write('save\nend\n')
  p.stdin.close()
  p.communicate()

def osxMergeLibraries(libs, src_dir, destination):
  cmd_file = tempfile.NamedTemporaryFile(suffix='.rsp', mode='w+t', delete=False)
  cmd_file.write('\n'.join([os.path.join(src_dir, l) for l in libs]))
  cmd_file.close()

  safeMakeDirs(os.path.dirname(destination))
  if subprocess.call(['libtool', '-static', '-o', destination, '-filelist', cmd_file.name ]) != 0:
    sys.exit(1)

def winMergeLibraries(libs, src_dir, destination):
  safeMakeDirs(os.path.dirname(destination))
  if subprocess.call(['lib.exe', '/OUT:' + destination ] + [os.path.join(src_dir, l) for l in libs]) != 0:
    sys.exit(1)

def copyFiles(src_dir, dst_dir, file_list, keep_src_path = True):
  for fname in file_list:
    if keep_src_path:
      dest = os.path.join(dst_dir, fname)
    else:
      dest = os.path.join(dst_dir, os.path.basename(fname))
    try:
      os.makedirs(os.path.dirname(dest))
    except OSError:
      pass

    full_fname = os.path.join(src_dir, fname)
    try:
      shutil.copy(full_fname, dest)
    except IOError:
      # third_party/libxslt/COPYING is an alias to itself
      print "ERROR: Could not copy \"%s\"; skipping..." % full_fname
      pass

# exts is an array of strings or regular expressions.
def findAllFilesWithExtension(dir, exts):
  print dir, exts
  file_list = []
  for root, dirs, files in os.walk(dir, followlinks=True):
    for filename in files:
      for ext in exts:
      	ok = False
      	if type(ext) is str:
      	  ok = filename.endswith(ext)
      	else:
      	  ok = ext.match(filename)
      	if ok:
          path = os.path.join(root, filename)
          file_list.append(os.path.relpath(path, dir))
          break
  return file_list

# 'configuration' is a valid configuration such as 'Debug' or 'Release'
def build(build_dir, configuration):
  out_dir = os.path.join(build_dir, configuration)
  args = [
    "is_debug=%s" % ('true' if configuration == 'Debug' else 'false'),
    "rtc_include_tests=false",
    "use_rtti=true",
  ]
  if mac:
    args.extend(
      [
        "is_component_build=false",
        "libyuv_include_tests=false",
        "rtc_enable_protobuf=false",
      ]
    )
  else:
    args.extend(
      [
        "rtc_enable_protobuf=false",
        "rtc_use_openmax_dl=false",
        "is_clang=false",
        "use_sysroot=false",
        "rtc_use_gtk=false",
      ]
    )
    if windows:
      args.append("target_cpu=\\\"x86\\\"")

  cmd = "gn gen %s --args=\"%s\"" % (out_dir, ' '.join(args))
  if subprocess.call(cmd, cwd=script_dir, shell=True) != 0:
    exit(1)

  if subprocess.call(['ninja', '-j5', '-C', out_dir], cwd=script_dir) != 0:
    exit(1)

def copy(src, dest_dir):
  dest_file = os.path.join(dest_dir, os.path.basename(src))
  if os.path.isdir(src):
    shutil.copytree(src, dest_file, symlinks=True)
  else:
    shutil.copy(src, dest_dir)

# Remove unneeded libraries from third_party.
def trimThirdParty():
  libs = [
    "boringssl",
    "expat",
    "gflags",
    "jsoncpp",
    "libjpeg_turbo",
    "libsrtp",
    "libvpx",
    "libyuv",
    "opus",
    "protobuf",
    "usrsctp",
    "yasm",
  ]
  if mac:
    libs.extend(
      [
        "llvm-build",
        "openmax_dl",
        "ocmock",
      ]
    )
  elif windows:
    libs.append("winsdk_samples")

  third_party_dir = os.path.join(script_dir, "third_party")
  third_party_old_dir = os.path.join(script_dir, "third_party.old")
  third_party_new_dir = os.path.join(script_dir, "third_party.new")
  if not os.path.isdir(third_party_old_dir) and os.path.isdir(third_party_dir):
    # No third_party_old_dir: either hasn't been run, or failed during copy to third_party_new_dir.
    # No third_party_dir: completed up to, but not including, rename of third_party_new_dir to third_party_dir.
    shutil.rmtree(third_party_new_dir, ignore_errors=True)
    os.makedirs(third_party_new_dir)

    copy(os.path.join(third_party_dir, "BUILD.gn"), third_party_new_dir)
    for lib in libs:
      copy(os.path.join(third_party_dir, lib), third_party_new_dir)

    os.rename(third_party_dir, third_party_old_dir)
  if os.path.isdir(third_party_new_dir):
    os.rename(third_party_new_dir, third_party_dir)

def main(argv):
  usage = "Usage: %prog [options]"

  default_platform = {
    'Windows': 'win32',
    'Linux': 'linux-x64',
    'Darwin': 'osx',
  }.get(platform.system(), None)

  parser = optparse.OptionParser(usage=usage)
  parser.add_option("--source_dir", dest="source_dir", default=source_dir, help="Location of the webrtc source directory (containing 'src')")
  parser.add_option("--build_dir", dest="build_dir", default=current_dir, help="Location of the webrtc build directory (containing 'Debug' and/or 'Release')")
  parser.add_option("--version", dest="version", default=None, help="Name to give to the webrtc build. It is recommended to use the format <date>-<git change number>")
  parser.add_option("--platform", dest="platform", default=default_platform, help="Platform to generate for (linux-x64, win32, osx, ...)")
  parser.add_option("-c", "--configuration", dest="configuration", default="Both", help="Configuration for webrtc (Debug, Release, or Both)")
  (options, args) = parser.parse_args(argv)

  required_options = [ "source_dir", "version", "platform" ]
  for k in required_options:
    if not options.__dict__.has_key(k) or not options.__dict__[k]:
      parser.error("Option '" + k + "' must be specified")

  print "Options values:"
  for k in options.__dict__:
    print "--%s=%s" % (k, options.__dict__[k])

  trimThirdParty()

  if options.configuration in ['Debug', 'Release']:
    build(options.build_dir, options.configuration)
  else:
    build(options.build_dir, 'Debug')
    build(options.build_dir, 'Release')

  packager = WebRTCPackager(
    options.source_dir,
    options.build_dir,
    options.version,
    options.platform,
    options.configuration
  )
  packager.buildPackage()
  packager.extractLibsFromNinjaFile()

if __name__ == "__main__":
  main(sys.argv)
