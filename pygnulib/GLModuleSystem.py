# Copyright (C) 2002-2024 Free Software Foundation, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations

#===============================================================================
# Define global imports
#===============================================================================
import os
import re
import sys
import codecs
import hashlib
import subprocess as sp
from . import constants
from .GLError import GLError
from .GLConfig import GLConfig
from .GLFileSystem import GLFileSystem


#===============================================================================
# Define module information
#===============================================================================
__author__ = constants.__author__
__license__ = constants.__license__
__copyright__ = constants.__copyright__


#===============================================================================
# Define global constants
#===============================================================================
DIRS = constants.DIRS
ENCS = constants.ENCS
TESTS = constants.TESTS
joinpath = constants.joinpath
subend = constants.subend
lines_to_multiline = constants.lines_to_multiline
isdir = os.path.isdir
isfile = os.path.isfile
filter_filelist = constants.filter_filelist


#===============================================================================
# Define GLModuleSystem class
#===============================================================================
class GLModuleSystem(object):
    '''GLModuleSystem is used to operate with module system using dynamic
    searching and patching.'''

    def __init__(self, config: GLConfig) -> None:
        '''Create new GLModuleSystem instance. Some functions use GLFileSystem class
        to look up a file in localpath or gnulib directories, or combine it through
        'patch' utility.'''
        self.args = dict()
        if type(config) is not GLConfig:
            raise TypeError('config must be a GLConfig, not %s'
                            % type(config).__name__)
        self.config = config
        self.filesystem = GLFileSystem(self.config)

    def __repr__(self) -> str:
        '''x.__repr__ <==> repr(x)'''
        result = '<pygnulib.GLModuleSystem %s>' % hex(id(self))
        return result

    def exists(self, module: str) -> bool:
        '''Check whether the given module exists.
        GLConfig: localpath.'''
        if type(module) is not str:
            raise TypeError('module must be a string, not %s'
                            % type(module).__name__)
        localpath = self.config['localpath']
        result = False
        badnames = ['ChangeLog', 'COPYING', 'README', 'TEMPLATE',
                    'TEMPLATE-EXTENDED', 'TEMPLATE-TESTS']
        if module not in badnames:
            result = isfile(joinpath(DIRS['modules'], module))
            if not result:
                for localdir in localpath:
                    if (isdir(joinpath(localdir, 'modules'))
                            and isfile(joinpath(localdir, 'modules', module))):
                        result = True
                        break
        return result

    def find(self, module: str) -> GLModule | None:
        '''Find the given module.'''
        if type(module) is not str:
            raise TypeError('module must be a string, not %s'
                            % type(module).__name__)
        if self.exists(module):
            path, istemp = self.filesystem.lookup(joinpath('modules', module))
            result = GLModule(self.config, path, istemp)
            return result
        else:  # if not self.exists(module)
            if self.config['errors']:
                raise GLError(3, module)
            else:  # if not self.config['errors']
                sys.stderr.write('gnulib-tool: warning: ')
                sys.stderr.write('file %s does not exist\n' % str(module))

    def file_is_module(self, filename: str) -> bool:
        '''Given the name of a file in the modules/ directory, return true
        if should be viewed as a module description file.'''
        return not (filename == 'ChangeLog' or filename.endswith('/ChangeLog')
                    or filename == 'COPYING' or filename.endswith('/COPYING')
                    or filename == 'README' or filename.endswith('/README')
                    or filename == 'TEMPLATE'
                    or filename == 'TEMPLATE-EXTENDED'
                    or filename == 'TEMPLATE-TESTS'
                    or filename.startswith('.')
                    or filename.endswith('.orig')
                    or filename.endswith('.rej')
                    or filename.endswith('~'))

    def list(self) -> list[str]:
        '''Return the available module names as tuple. We could use a combination
        of os.walk() function and re module. However, it takes too much time to
        complete, so this version uses subprocess to run shell commands.'''
        result = ''
        listing = list()
        localpath = self.config['localpath']
        find_args = ['find', 'modules', '-type', 'f', '-print']

        # Read modules from gnulib root directory.
        os.chdir(constants.DIRS['root'])
        find = sp.Popen(find_args, stdout=sp.PIPE)
        result += find.stdout.read().decode("UTF-8")
        os.chdir(DIRS['cwd'])

        # Read modules from local directories.
        if len(localpath) > 0:
            for localdir in localpath:
                os.chdir(localdir)
                find = sp.Popen(find_args, stdout=sp.PIPE)
                result += find.stdout.read().decode("UTF-8")
                os.chdir(DIRS['cwd'])

        listing = [ line
                    for line in result.split('\n')
                    if line.strip() ]
        if len(localpath) > 0:
            listing = [ subend('.diff', '', line)
                        for line in listing ]
        # Remove modules/ prefix from each file name.
        pattern = re.compile(r'^modules/')
        listing = [ pattern.sub(r'', line)
                    for line in listing ]
        # Filter out undesired file names.
        listing = [ line
                    for line in listing
                    if self.file_is_module(line) and not line.endswith('-tests') ]
        modules = sorted(set(listing))
        return modules


#===============================================================================
# Define GLModule class
#===============================================================================
class GLModule(object):
    '''GLModule is used to create a module object from the file with the given
    path. GLModule can get all information about module, get its dependencies,
    files, etc.'''

    section_label_pattern = \
        re.compile(r'^(Description|Comment|Status|Notice|Applicability|'
                   + r'Files|Depends-on|configure\.ac-early|configure\.ac|'
                   + r'Makefile\.am|Include|Link|License|Maintainer):$',
                   re.M)

    # List of characters allowed in shell identifiers.
    shell_id_chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_'

    def __init__(self, config: GLConfig, path: str, patched: bool = False) -> None:
        '''Create new GLModule instance. Arguments are path and patched, where
        path is a string representing the path to the module and patched is a
        bool indicating that module was created after applying patch.'''
        self.args = dict()
        self.cache = dict()
        self.content = ''
        if type(config) is not GLConfig:
            raise TypeError('config must be a GLConfig, not %s'
                            % type(config).__name__)
        if type(path) is not str:
            raise TypeError('path must be a string, not %s'
                            % type(path).__name__)
        if type(patched) is not bool:
            raise TypeError('patched must be a bool, not %s'
                            % type(patched).__name__)
        self.path = path
        self.patched = patched
        self.config = config
        self.filesystem = GLFileSystem(self.config)
        self.modulesystem = GLModuleSystem(self.config)
        # Read the module description file into memory.
        with codecs.open(path, 'rb', 'UTF-8') as file:
            self.content = file.read().replace('\r\n', '\n')
        # Dissect it into sections.
        self.sections = dict()
        last_section_label = None
        last_section_start = 0
        for match in GLModule.section_label_pattern.finditer(self.content):
            if last_section_label != None:
                self.sections[last_section_label] = self.content[last_section_start : match.start()]
            last_section_label = match.group(1)
            last_section_start = match.end() + 1
        if last_section_label != None:
            self.sections[last_section_label] = self.content[last_section_start:]

    def __eq__(self, module: object) -> bool:
        '''x.__eq__(y) <==> x==y'''
        result = False
        if type(module) is GLModule:
            if self.path == module.path:
                result = True
        return result

    def __ne__(self, module: object) -> bool:
        '''x.__ne__(y) <==> x!=y'''
        result = False
        if type(module) is GLModule:
            if self.path != module.path:
                result = True
        return result

    def __ge__(self, module: object) -> bool:
        '''x.__ge__(y) <==> x>=y'''
        result = False
        if type(module) is GLModule:
            if self.path >= module.path:
                result = True
        return result

    def __gt__(self, module: object) -> bool:
        '''x.__gt__(y) <==> x>y'''
        result = False
        if type(module) is GLModule:
            if self.path > module.path:
                result = True
        return result

    def __hash__(self) -> int:
        '''x.__hash__() <==> hash(x)'''
        result = hash(self.path) ^ hash(self.patched)
        return result

    def __le__(self, module: object) -> bool:
        '''x.__le__(y) <==> x<=y'''
        result = False
        if type(module) is GLModule:
            if self.path <= module.path:
                result = True
        return result

    def __lt__(self, module: object) -> bool:
        '''x.__lt__(y) <==> x<y'''
        result = False
        if type(module) is GLModule:
            if self.path < module.path:
                result = True
        return result

    def __str__(self) -> str:
        '''x.__str__() <==> str(x)'''
        result = self.getName()
        return result

    def __repr__(self) -> str:
        '''x.__repr__ <==> repr(x)'''
        result = '<pygnulib.GLModule %s %s>' % (repr(self.getName()), hex(id(self)))
        return result

    def getName(self) -> str:
        '''Return the name of the module.'''
        pattern = re.compile(joinpath('modules', '(.*)$'))
        result = pattern.findall(self.path)[0]
        return result

    def isPatched(self) -> bool:
        '''Check whether module was created after applying patch.'''
        return self.patched

    def isTests(self) -> bool:
        '''Check whether module is a *-tests module or a module of
        applicability 'all'.'''
        result = self.getApplicability() != 'main'
        return result

    def isNonTests(self) -> bool:
        '''Check whether module is not a *-tests module.'''
        result = not self.getName().endswith('-tests')
        return result

    def getTestsName(self) -> str:
        '''Return -tests version of the module name.'''
        result = self.getName()
        if not result.endswith('-tests'):
            result += '-tests'
        return result

    def getTestsModule(self) -> GLModule | None:
        '''Return -tests version of the module as GLModule.'''
        result = self.modulesystem.find(self.getTestsName())
        return result

    def repeatModuleInTests(self) -> bool:
        '''Tests whether, when the tests have their own configure.ac script,
        a given module should be repeated in the tests, although it was
        already among the main modules.'''
        # This module is special because it relies on a gl_LIBTEXTSTYLE_OPTIONAL
        # invocation that it does not itself do or require. Therefore if the
        # tests contain such an invocation, the module - as part of tests -
        # will produce different AC_SUBSTed variable values than the same module
        # - as part of the main configure.ac -.
        result = self.getName() == 'libtextstyle-optional'
        return result

    def getDependenciesRecursively(self) -> str:
        '''Return a list of recursive dependencies of this module separated
        by a newline.'''
        handledmodules = set()
        inmodules = set()
        outmodules = set()

        # In order to process every module only once (for speed), process an "input
        # list" of modules, producing an "output list" of modules. During each round,
        # more modules can be queued in the input list. Once a module on the input
        # list has been processed, it is added to the "handled list", so we can avoid
        # to process it again.
        inmodules.add(self)
        while len(inmodules) > 0:
            inmodules_this_round = inmodules
            inmodules = set()  # Accumulator, queue for next round
            for module in inmodules_this_round:
                outmodules.add(module)
                inmodules = inmodules.union(module.getDependenciesWithoutConditions())
            handledmodules = handledmodules.union(inmodules_this_round)
            # Remove handledmodules from inmodules.
            inmodules = inmodules.difference(handledmodules)

        module_names = sorted([ str(module)
                                for module in outmodules ])
        return lines_to_multiline(module_names)

    def getLinkDirectiveRecursively(self) -> str:
        '''Return a list of the link directives of this module separated
        by a newline.'''
        handledmodules = set()
        inmodules = set()
        outmodules = set()

        # In order to process every module only once (for speed), process an "input
        # list" of modules, producing an "output list" of modules. During each round,
        # more modules can be queued in the input list. Once a module on the input
        # list has been processed, it is added to the "handled list", so we can avoid
        # to process it again.
        inmodules.add(self)
        while len(inmodules) > 0:
            inmodules_this_round = inmodules
            inmodules = set()  # Accumulator, queue for next round
            for module in inmodules_this_round:
                if module.getLink() != '':
                    # The module description has a 'Link:' field. Ignore the dependencies.
                    outmodules.add(module)
                else:
                    # The module description has no 'Link:' field. Recurse through the dependencies.
                    inmodules = inmodules.union(module.getDependenciesWithoutConditions())
            handledmodules = handledmodules.union(inmodules_this_round)
            # Remove handledmodules from inmodules.
            inmodules = inmodules.difference(handledmodules)

        # Remove whitespace from sections.
        link_sections = [ module.getLink().strip()
                          for module in outmodules ]
        # Sort the link directives.
        directives = sorted({ line
                              for section in link_sections
                              for line in section.splitlines() })
        return lines_to_multiline(directives)

    def getShellFunc(self) -> str:
        '''Computes the shell function name that will contain the m4 macros
        for the module.'''
        macro_prefix = self.config['macro_prefix']
        valid_shell_id = True
        for char in self.getName():
            if char not in GLModule.shell_id_chars:
                valid_shell_id = False
                break
        identifier = None
        if valid_shell_id:
            identifier = self.getName()
        else:
            hash_input = '%s\n' % self.getName()
            identifier = hashlib.md5(hash_input.encode(ENCS['default'])).hexdigest()
        result = 'func_%s_gnulib_m4code_%s' % (macro_prefix, identifier)
        return result

    def getShellVar(self) -> str:
        '''Compute the shell variable name the will be set to true once the
        m4 macros for the module have been executed.'''
        macro_prefix = self.config['macro_prefix']
        valid_shell_id = True
        for char in self.getName():
            if char not in GLModule.shell_id_chars:
                valid_shell_id = False
                break
        identifier = None
        if valid_shell_id:
            identifier = self.getName()
        else:
            hash_input = '%s\n' % self.getName()
            identifier = hashlib.md5(hash_input.encode(ENCS['default'])).hexdigest()
        result = '%s_gnulib_enabled_%s' % (macro_prefix, identifier)
        return result

    def getConditionalName(self) -> str:
        '''Return the automake conditional name.
        GLConfig: macro_prefix.'''
        macro_prefix = self.config['macro_prefix']
        valid_shell_id = True
        for char in self.getName():
            if char not in GLModule.shell_id_chars:
                valid_shell_id = False
                break
        identifier = None
        if valid_shell_id:
            identifier = self.getName()
        else:
            hash_input = '%s\n' % self.getName()
            identifier = hashlib.md5(hash_input.encode(ENCS['default'])).hexdigest()
        result = '%s_GNULIB_ENABLED_%s' % (macro_prefix, identifier)
        return result

    def getDescription(self) -> str:
        '''Return description of the module.'''
        return self.sections.get('Description', '')

    def getComment(self) -> str:
        '''Return comment to module.'''
        return self.sections.get('Comment', '')

    def getStatus(self) -> str:
        '''Return module status.'''
        return self.sections.get('Status', '')

    def getStatuses(self) -> list[str]:
        '''Return module status.'''
        if 'statuses' not in self.cache:
            snippet = self.getStatus()
            result = [ line.strip()
                       for line in snippet.split('\n')
                       if line.strip() ]
            self.cache['statuses'] = result
        return self.cache['statuses']

    def getNotice(self) -> str:
        '''Return notice to module.'''
        return self.sections.get('Notice', '')

    def getApplicability(self) -> str:
        '''Return applicability of module.'''
        if 'applicability' not in self.cache:
            result = self.sections.get('Applicability', '')
            result = result.strip()
            if not result:
                # The default is 'main' or 'tests', depending on the module's name.
                if self.getName().endswith('-tests'):
                    result = 'tests'
                else:
                    result = 'main'
            self.cache['applicability'] = result
        return self.cache['applicability']

    def getFiles_Raw(self) -> str:
        '''Return the unmodified list of files as a string.'''
        return self.sections.get('Files', '')

    def getFiles(self) -> list[str]:
        '''Return list of files.
        GLConfig: ac_version.'''
        if 'files' not in self.cache:
            snippet = self.getFiles_Raw()
            result = [ line.strip()
                       for line in snippet.split('\n')
                       if line.strip() ]
            result.append(joinpath('m4', '00gnulib.m4'))
            result.append(joinpath('m4', 'zzgnulib.m4'))
            result.append(joinpath('m4', 'gnulib-common.m4'))
            self.cache['files'] = result
        return self.cache['files']

    def getDependencies(self) -> str:
        '''Return list of dependencies, as a snippet.
        GLConfig: localpath.'''
        if 'dependencies' not in self.cache:
            result = ''
            # ${module}-tests implicitly depends on ${module}, if that module exists.
            if self.getName().endswith('-tests'):
                main_module = subend('-tests', '', self.getName())
                if self.modulesystem.exists(main_module):
                    result += '%s\n' % main_module
            # Then the explicit dependencies listed in the module description.
            snippet = self.sections.get('Depends-on', '')
            # Remove comment lines.
            snippet = re.compile(r'^#.*$[\n]', re.M).sub(r'', snippet)
            result += snippet
            self.cache['dependencies'] = result
        return self.cache['dependencies']

    def getDependenciesWithoutConditions(self) -> list[GLModule | None]:
        '''Return list of dependencies, as a list of GLModule objects.
        GLConfig: localpath.'''
        if 'dependenciesWithoutCond' not in self.cache:
            snippet = self.getDependencies()
            lines = [ line.strip()
                      for line in snippet.split('\n')
                      if line.strip() ]
            pattern = re.compile(r' *\[.*$')
            lines = [ pattern.sub(r'', line)
                      for line in lines ]
            result = [ self.modulesystem.find(module)
                       for module in lines
                       if module != '' ]
            self.cache['dependenciesWithoutCond'] = result
        return self.cache['dependenciesWithoutCond']

    def getDependenciesWithConditions(self) -> list[tuple[GLModule, str | None]]:
        '''Return list of dependencies, as a list of pairs (GLModule object, condition).
        The "true" condition is denoted by None.
        GLConfig: localpath.'''

        if 'dependenciesWithCond' not in self.cache:
            snippet = self.getDependencies()
            lines = [ line.strip()
                      for line in snippet.split('\n')
                      if line.strip() ]
            pattern = re.compile(r' *\[')
            result = []
            for line in lines:
                match = pattern.search(line)
                if match:
                    module = line[0 : match.start()]
                    condition = line[match.end() :]
                    condition = subend(']', '', condition)
                else:
                    module = line
                    condition = None
                if module != '':
                    if condition == 'true':
                        condition = None
                    result.append(tuple([self.modulesystem.find(module), condition]))
            self.cache['dependenciesWithCond'] = result
        return self.cache['dependenciesWithCond']

    def getAutoconfEarlySnippet(self) -> str:
        '''Return autoconf-early snippet.'''
        return self.sections.get('configure.ac-early', '')

    def getAutoconfSnippet(self) -> str:
        '''Return autoconf snippet.'''
        return self.sections.get('configure.ac', '')

    def getAutomakeSnippet(self) -> str:
        '''Get automake snippet.
        GLConfig: auxdir, ac_version.'''
        result = ''
        conditional = self.getAutomakeSnippet_Conditional()
        if conditional.strip():
            result += self.getAutomakeSnippet_Conditional()
        else:  # if not conditional.strip()
            result += '\n'
        result += self.getAutomakeSnippet_Unconditional()
        return result

    def getAutomakeSnippet_Conditional(self) -> str:
        '''Return conditional automake snippet.'''
        return self.sections.get('Makefile.am', '')

    def getAutomakeSnippet_Unconditional(self) -> str:
        '''Return unconditional automake snippet.
        GLConfig: auxdir, ac_version.'''
        auxdir = self.config['auxdir']
        ac_version = self.config['ac_version']
        result = ''
        if 'makefile-unconditional' not in self.cache:
            if self.getName().endswith('-tests'):
                # *-tests module live in tests/, not lib/.
                # Synthesize an EXTRA_DIST augmentation.
                files = self.getFiles()
                extra_files = filter_filelist(constants.NL, files,
                                              'tests/', '', 'tests/', '')
                if extra_files != '':
                    result += 'EXTRA_DIST += %s' % ' '.join(extra_files.split(constants.NL))
                    result += constants.NL * 2
            else:  # if not tests module
                # Synthesize an EXTRA_DIST augmentation.
                snippet = self.getAutomakeSnippet_Conditional()
                snippet = constants.combine_lines(snippet)
                pattern = re.compile(r'^lib_SOURCES[\t ]*\+=[\t ]*(.*)$', re.MULTILINE)
                mentioned_files = set(pattern.findall(snippet))
                if mentioned_files:
                    # Get all the file names from 'lib_SOURCES += ...'.
                    mentioned_files = { filename
                                        for line in mentioned_files
                                        for filename in line.split() }
                all_files = self.getFiles()
                lib_files = filter_filelist(constants.NL, all_files,
                                            'lib/', '', 'lib/', '')
                if lib_files != '':
                    lib_files = set(lib_files.split(constants.NL))
                else:
                    lib_files = set()
                # Remove mentioned_files from lib_files.
                extra_files = sorted(lib_files.difference(mentioned_files))
                if extra_files:
                    result += 'EXTRA_DIST += %s' % ' '.join(extra_files)
                    result += '\n\n'
                # Synthesize also an EXTRA_lib_SOURCES augmentation.
                # This is necessary so that automake can generate the right list of
                # dependency rules.
                # A possible approach would be to use autom4te --trace of the redefined
                # AC_LIBOBJ and AC_REPLACE_FUNCS macros when creating the Makefile.am
                # (use autom4te --trace, not just grep, so that AC_LIBOBJ invocations
                # inside autoconf's built-in macros are not missed).
                # But it's simpler and more robust to do it here, based on the file list.
                # If some .c file exists and is not used with AC_LIBOBJ - for example,
                # a .c file is preprocessed into another .c file for BUILT_SOURCES -,
                # automake will generate a useless dependency; this is harmless.
                if str(self) != 'relocatable-prog-wrapper' and str(self) != 'pt_chown':
                    extra_files = filter_filelist(constants.NL, extra_files,
                                                  '', '.c', '', '')
                    if extra_files != '':
                        result += 'EXTRA_lib_SOURCES += %s' % ' '.join(extra_files.split(constants.NL))
                        result += '\n\n'
                # Synthesize an EXTRA_DIST augmentation also for the files in build-aux
                buildaux_files = filter_filelist(constants.NL, all_files,
                                                 'build-aux/', '', 'build-aux/', '')
                if buildaux_files != '':
                    buildaux_files = [ joinpath('$(top_srcdir)', auxdir, filename)
                                       for filename in buildaux_files.split(constants.NL) ]
                    result += 'EXTRA_DIST += %s' % ' '.join(buildaux_files)
                    result += '\n\n'
            result = constants.nlconvert(result)
            self.cache['makefile-unconditional'] = result
        return self.cache['makefile-unconditional']

    def getInclude(self) -> str:
        '''Return include directive.'''
        if 'include' not in self.cache:
            snippet = self.sections.get('Include', '')
            pattern = re.compile(r'^(["<])', re.M)
            result = pattern.sub(r'#include \1', snippet)
            self.cache['include'] = result
        return self.cache['include']

    def getLink(self) -> str:
        '''Return link directive.'''
        return self.sections.get('Link', '')

    def getLicense_Raw(self) -> str:
        '''Return module license.'''
        return self.sections.get('License', '')

    def getLicense(self) -> str:
        '''Get license and warn user if module lacks a license.'''
        if 'license' not in self.cache:
            license = self.getLicense_Raw().strip()
            # Warn if the License field is missing.
            if not self.getName().endswith('-tests'):
                if not license:
                    if self.config['errors']:
                        raise GLError(18, str(self))
                    else:  # if not self.config['errors']
                        sys.stderr.write('gnulib-tool: warning: module %s lacks a License\n' % str(self))
            if str(self).startswith('parse-datetime'):
                # These modules are under a weaker license only for the purpose of some
                # users who hand-edit it and don't use gnulib-tool. For the regular
                # gnulib users they are under a stricter license.
                result = 'GPL'
            else:
                result = license
                # The default is GPL.
                if not result:
                    result = 'GPL'
            self.cache['license'] = result
        return self.cache['license']

    def getMaintainer(self) -> str:
        '''Return maintainer directive.'''
        return self.sections.get('Maintainer', '')


#===============================================================================
# Define GLModuleTable class
#===============================================================================
class GLModuleTable(object):
    '''GLModuleTable is used to work with the list of the modules.'''

    def __init__(self, config: GLConfig, inc_all_direct_tests: bool, inc_all_indirect_tests: bool) -> None:
        '''Create new GLModuleTable instance. If modules are specified, then add
        every module from iterable as unconditional module. If avoids is specified,
        then in transitive_closure every dependency which is in avoids won't be
        included in the final modules list. If conddeps are enabled,
        then store condition for each dependency if it has a condition.
        The only necessary argument is localpath, which is needed just to create
        modulesystem instance to look for dependencies.
        inc_all_direct_tests = True if all kinds of problematic unit tests among
                                    the unit tests of the specified modules
                                    should be included
        inc_all_indirect_tests = True if all kinds of problematic unit tests
                                    among the unit tests of the dependencies
                                    should be included

        Methods for conditional dependencies:
        - addUnconditional(B)
          notes the presence of B as an unconditional module.
        - addConditional(A, B. cond)
          notes the presence of a conditional dependency from module A to module B,
          subject to the condition that A is enabled and cond is true.
        - isConditional(B)
          tests whether module B is conditional.
        - getCondition(A, B)
          returns the condition when B should be enabled as a dependency of A,
          once the m4 code for A has been executed.
        '''
        self.dependers = dict()  # Dependencies
        self.conditionals = dict()  # Conditional modules
        self.unconditionals = dict()  # Unconditional modules
        self.base_modules = list()  # Base modules
        self.main_modules = list()  # Main modules
        self.tests_modules = list()  # Tests modules
        self.final_modules = list()  # Final modules
        if type(config) is not GLConfig:
            raise TypeError('config must be a GLConfig, not %s'
                            % type(config).__name__)
        self.config = config
        self.filesystem = GLFileSystem(self.config)
        self.modulesystem = GLModuleSystem(self.config)
        if type(inc_all_direct_tests) is not bool:
            raise TypeError('inc_all_direct_tests must be a bool, not %s'
                            % type(inc_all_direct_tests).__name__)
        self.inc_all_direct_tests = inc_all_direct_tests
        self.inc_all_indirect_tests = inc_all_indirect_tests
        self.avoids = list()  # Avoids
        for avoid in self.config.getAvoids():
            module = self.modulesystem.find(avoid)
            if module:
                self.avoids.append(module)

    def __repr__(self) -> str:
        '''x.__repr__() <==> repr(x)'''
        result = '<pygnulib.GLModuleTable %s>' % hex(id(self))
        return result

    def __getitem__(self, y: str) -> list[GLModule]:
        '''x.__getitem__(y) <==> x[y]'''
        if y in ['base', 'final', 'main', 'tests', 'avoids']:
            if y == 'base':
                return self.getBaseModules()
            elif y == 'final':
                return self.getFinalModules()
            elif y == 'main':
                return self.getMainModules()
            elif y == 'tests':
                return self.getTestsModules()
            else:  # if y == 'avoids'
                return self.getAvoids()
        else:  # if y is not in list
            raise KeyError('GLModuleTable does not contain key: %s' % repr(y))

    def addConditional(self, parent: GLModule, module: GLModule, condition: str | bool) -> None:
        '''Add new conditional dependency from parent to module with condition.'''
        if type(parent) is not GLModule:
            raise TypeError('parent must be a GLModule, not %s'
                            % type(parent).__name__)
        if type(module) is not GLModule:
            raise TypeError('module must be a GLModule, not %s'
                            % type(module).__name__)
        if not (type(condition) is str or condition == True):
            raise TypeError('condition must be a string or True, not %s'
                            % type(condition).__name__)
        if not str(module) in self.unconditionals:
            # No unconditional dependency to the given module is known at this point.
            if str(module) not in self.dependers:
                self.dependers[str(module)] = list()
            if str(parent) not in self.dependers[str(module)]:
                self.dependers[str(module)].append(str(parent))
            key = '%s---%s' % (str(parent), str(module))
            self.conditionals[key] = condition

    def addUnconditional(self, module: GLModule) -> None:
        '''Add module as unconditional dependency.'''
        if type(module) is not GLModule:
            raise TypeError('module must be a GLModule, not %s'
                            % type(module).__name__)
        self.unconditionals[str(module)] = True
        if str(module) in self.dependers:
            self.dependers.pop(str(module))

    def isConditional(self, module: GLModule) -> bool:
        '''Check whether module is unconditional.'''
        if type(module) is not GLModule:
            raise TypeError('module must be a GLModule, not %s'
                            % type(module).__name__)
        result = str(module) in self.dependers
        return result

    def getCondition(self, parent: GLModule, module: GLModule) -> str | bool:
        '''Return condition from parent to module. Condition can be string or True.
        If module is not in the list of conddeps, method returns None.'''
        if type(parent) is not GLModule:
            raise TypeError('parent must be a GLModule, not %s'
                            % type(parent).__name__)
        if type(module) is not GLModule:
            raise TypeError('module must be a GLModule, not %s'
                            % type(module).__name__)
        key = '%s---%s' % (str(parent), str(module))
        result = self.conditionals.get(key, None)
        return result

    def transitive_closure(self, modules: list[GLModule]) -> list[GLModule]:
        '''Use transitive closure to add module and its dependencies. Add every
        module and its dependencies from modules list, but do not add dependencies
        which contain in avoids list. If any incl_test_categories is enabled, then
        add dependencies which are in these categories. If any excl_test_categories,
        then do not add dependencies which are in these categories. If conddeps are enabled,
        then store condition for each dependency if it has a condition. This method
        is used to update final list of modules. Method returns list of modules.
        GLConfig: incl_test_categories, excl_test_categories.'''
        for module in modules:
            if type(module) is not GLModule:
                raise TypeError('each module must be a GLModule instance')
        # In order to process every module only once (for speed), process an
        # "input list" of modules, producing an "output list" of modules. During
        # each round, more modules can be queued in the input list. Once a
        # module on the input list has been processed, it is added to the
        # "handled list", so we can avoid to process it again.
        inc_all_tests = self.inc_all_direct_tests
        handledmodules = list()
        inmodules = modules
        outmodules = list()
        if self.config['conddeps']:
            for module in modules:
                if module not in self.avoids:
                    self.addUnconditional(module)
        while inmodules:
            inmodules_this_round = inmodules
            inmodules = list()               # Accumulator, queue for next round
            for module in inmodules_this_round:
                if module not in self.avoids:
                    outmodules += [module]
                    if self.config['conddeps']:
                        conditional = self.isConditional(module)
                    dependencies = module.getDependenciesWithConditions()
                    depmodules = [ pair[0]
                                   for pair in dependencies ]
                    conditions = [ pair[1]
                                   for pair in dependencies ]
                    # Duplicate dependencies are harmless, but Jim wants a warning.
                    duplicate_depmodules = [ depmodule
                                             for depmodule in set(depmodules)
                                             if depmodules.count(depmodule) > 1 ]
                    if duplicate_depmodules:
                        duplicate_depmodule_names = [ str(depmodule)
                                                      for depmodule in duplicate_depmodules ]
                        message = ('gnulib-tool: warning: module %s has duplicated dependencies: %s\n'
                                   % (module, duplicate_depmodule_names))
                        sys.stderr.write(message)
                    if self.config.checkInclTestCategory(TESTS['tests']):
                        testsname = module.getTestsName()
                        if self.modulesystem.exists(testsname):
                            testsmodule = self.modulesystem.find(testsname)
                            depmodules += [testsmodule]
                            conditions += [None]
                    for depmodule in depmodules:
                        # Determine whether to include the dependency or tests module.
                        include = True
                        statuses = depmodule.getStatuses()
                        for word in statuses:
                            if word == 'obsolete':
                                if not self.config.checkInclTestCategory(TESTS['obsolete']):
                                    include = False
                            elif word == 'c++-test':
                                if self.config.checkExclTestCategory(TESTS['c++-test']):
                                    include = False
                                if not (inc_all_tests or self.config.checkInclTestCategory(TESTS['c++-test'])):
                                    include = False
                            elif word == 'longrunning-test':
                                if self.config.checkExclTestCategory(TESTS['longrunning-test']):
                                    include = False
                                if not (inc_all_tests or self.config.checkInclTestCategory(TESTS['longrunning-test'])):
                                    include = False
                            elif word == 'privileged-test':
                                if self.config.checkExclTestCategory(TESTS['privileged-test']):
                                    include = False
                                if not (inc_all_tests or self.config.checkInclTestCategory(TESTS['privileged-test'])):
                                    include = False
                            elif word == 'unportable-test':
                                if self.config.checkExclTestCategory(TESTS['unportable-test']):
                                    include = False
                                if not (inc_all_tests or self.config.checkInclTestCategory(TESTS['unportable-test'])):
                                    include = False
                            elif word.endswith('-test'):
                                if not inc_all_tests:
                                    include = False
                        if include and depmodule not in self.avoids:
                            inmodules += [depmodule]
                            if self.config['conddeps']:
                                index = depmodules.index(depmodule)
                                condition = conditions[index]
                                if condition == True:
                                    condition = None
                                if condition:
                                    self.addConditional(module, depmodule, condition)
                                else:  # if condition
                                    if conditional:
                                        self.addConditional(module, depmodule, True)
                                    else:  # if not conditional
                                        self.addUnconditional(depmodule)
            handledmodules = sorted(set(handledmodules + inmodules_this_round))
            # Remove handledmodules from inmodules.
            inmodules = [module
                         for module in inmodules
                         if module not in handledmodules]
            inmodules = sorted(set(inmodules))
            inc_all_tests = self.inc_all_indirect_tests
        modules = sorted(set(outmodules))
        self.modules = modules
        return list(modules)

    def transitive_closure_separately(self, basemodules: list[GLModule],
                                      finalmodules: list[GLModule]) -> tuple[list[GLModule], list[GLModule]]:
        '''Determine main module list and tests-related module list separately.
        The main module list is the transitive closure of the specified modules,
        ignoring tests modules. Its lib/* sources go into $sourcebase/. If lgpl is
        specified, it will consist only of LGPLed source.
        The tests-related module list is the transitive closure of the specified
        modules, including tests modules, minus the main module list excluding
        modules of applicability 'all'. Its lib/* sources (brought in through
        dependencies of *-tests modules) go into $testsbase/. It may contain GPLed
        source, even if lgpl is specified.
        Arguments are basemodules and finalmodules, where basemodules argument
        represents modules specified by user and finalmodules represents modules
        list after previous transitive_closure.
        Method returns tuple which contains two lists: the list of main modules and
        the list of tests-related modules. Both lists contain dependencies.
        GLConfig: incl_test_categories, excl_test_categories.'''
        for module in basemodules:
            if type(module) is not GLModule:
                raise TypeError('each module must be a GLModule instance')
        for module in finalmodules:
            if type(module) is not GLModule:
                raise TypeError('each module must be a GLModule instance')
        # Determine main module list.
        saved_inctests = self.config.checkInclTestCategory(TESTS['tests'])
        self.config.disableInclTestCategory(TESTS['tests'])
        main_modules = self.transitive_closure(basemodules)
        self.config.setInclTestCategory(TESTS['tests'], saved_inctests)
        # Determine tests-related module list.
        tests_modules = \
            [ m
              for m in finalmodules
              if not (m in main_modules and m.getApplicability() == 'main') ]
        # Note: Since main_modules is (hopefully) a subset of finalmodules, this
        # ought to be the same as
        #   [ m
        #     for m in finalmodules
        #     if m not in main_modules ] \
        #   + [ m
        #       for m in main_modules
        #       if m.getApplicability() != 'main' ]
        tests_modules = sorted(list(set(tests_modules)))
        # If testsrelated_modules consists only of modules with applicability 'all',
        # set it to empty (because such modules are only helper modules for other modules).
        have_nontrivial_testsrelated_modules = False
        for module in tests_modules:
            if module.getApplicability() != 'all':
                have_nontrivial_testsrelated_modules = True
                break
        if not have_nontrivial_testsrelated_modules:
            tests_modules = []
        result = tuple([main_modules, tests_modules])
        return result

    def remove_if_blocks(self, snippet: str) -> str:
        '''Removes if...endif blocks from an automake snippet.'''
        lines = snippet.splitlines()
        cleansed = []
        depth = 0
        for line in lines:
            if line.startswith('if '):
                depth += 1
            elif line.startswith('endif'):
                depth -= 1
                # Make sure gnulib-tool.py and gnulib-tool.sh produce the same
                # output.
                cleansed.append(line[5:])
            elif depth == 0:
                cleansed.append(line)
        return lines_to_multiline(cleansed)

    def add_dummy(self, modules: list[GLModule]) -> list[GLModule]:
        '''Add dummy package to list of modules if dummy package is needed.
        If not, return original list of modules.
        GLConfig: auxdir, ac_version, conddeps.'''
        auxdir = self.config['auxdir']
        ac_version = self.config['ac_version']
        conddeps = self.config['conddeps']
        for module in modules:
            if type(module) is not GLModule:
                raise TypeError('each module must be a GLModule instance')
        # Determine whether any module provides a lib_SOURCES augmentation.
        have_lib_sources = False
        for module in modules:
            if module.isNonTests():
                if conddeps and self.isConditional(module):
                    # Ignore conditional modules, since they are not guaranteed to
                    # contribute to lib_SOURCES.
                    pass
                else:
                    snippet = module.getAutomakeSnippet()
                    # Extract the value of unconditional "lib_SOURCES += ..." augmentations.
                    snippet = constants.remove_backslash_newline(snippet)
                    snippet = self.remove_if_blocks(snippet)
                    pattern = re.compile(r'^lib_SOURCES[\t ]*\+=([^#]*).*$', re.M)
                    for matching_rhs in pattern.findall(snippet):
                        files = matching_rhs.split(' ')
                        for file in files:
                            # Ignore .h files since they are not compiled.
                            if not file.endswith('.h'):
                                have_lib_sources = True
                                break
        # Add the dummy module, to make sure the library will be non-empty.
        if not have_lib_sources:
            dummy = self.modulesystem.find('dummy')
            if dummy not in self.avoids:
                if dummy not in modules:
                    modules = sorted(set(modules)) + [dummy]
        return list(modules)

    def filelist(self, modules: list[GLModule]) -> list[str]:
        '''Determine the final file list for the given list of modules.
        The list of modules must already include dependencies.
        GLConfig: ac_version.'''
        ac_version = self.config['ac_version']
        filelist = list()
        for module in modules:
            if type(module) is not GLModule:
                raise TypeError('each module must be a GLModule instance')
        listings = [ module.getFiles()
                     for module in modules ]
        for listing in listings:
            for file in listing:
                if file not in filelist:
                    filelist += [file]
        return filelist

    def filelist_separately(self, main_modules: list[GLModule],
                            tests_modules: list[GLModule]) -> tuple[list[str], list[str]]:
        '''Determine the final file lists. They must be computed separately, because
        files in lib/* go into $sourcebase/ if they are in the main file list but
        into $testsbase/ if they are in the tests-related file list. Furthermore
        lib/dummy.c can be in both.'''
        ac_version = self.config['ac_version']
        main_filelist = self.filelist(main_modules)
        tests_filelist = self.filelist(tests_modules)
        tests_filelist = [ file.replace('lib/', 'tests=lib/', 1) if file.startswith('lib/') else file
                           for file in tests_filelist ]
        result = tuple([main_filelist, tests_filelist])
        return result

    def getAvoids(self) -> list[GLModule]:
        '''Return list of avoids.'''
        return list(self.avoids)

    def setAvoids(self, modules: list[GLModule]) -> None:
        '''Specify list of avoids.'''
        for module in modules:
            if type(module) is not GLModule:
                raise TypeError('each module must be a GLModule instance')
        self.avoids = sorted(set(modules))

    def getBaseModules(self) -> list[GLModule]:
        '''Return list of base modules.'''
        return list(self.base_modules)

    def setBaseModules(self, modules: list[GLModule]) -> None:
        '''Specify list of base modules.'''
        for module in modules:
            if type(module) is not GLModule:
                raise TypeError('each module must be a GLModule instance')
        self.base_modules = sorted(set(modules))

    def getFinalModules(self) -> list[GLModule]:
        '''Return list of final modules.'''
        return list(self.final_modules)

    def setFinalModules(self, modules: list[GLModule]) -> None:
        '''Specify list of final modules.'''
        for module in modules:
            if type(module) is not GLModule:
                raise TypeError('each module must be a GLModule instance')
        self.final_modules = sorted(set(modules))

    def getMainModules(self) -> list[GLModule]:
        '''Return list of main modules.'''
        return list(self.main_modules)

    def setMainModules(self, modules: list[GLModule]) -> None:
        '''Specify list of main modules.'''
        for module in modules:
            if type(module) is not GLModule:
                raise TypeError('each module must be a GLModule instance')
        self.main_modules = sorted(set(modules))

    def getTestsModules(self) -> list[GLModule]:
        '''Return list of tests modules.'''
        return list(self.tests_modules)

    def setTestsModules(self, modules: list[GLModule]) -> None:
        '''Specify list of tests modules.'''
        for module in modules:
            if type(module) is not GLModule:
                raise TypeError('each module must be a GLModule instance')
        self.tests_modules = sorted(set(modules))
