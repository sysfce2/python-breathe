
from docutils import nodes
from docutils.parsers.rst.directives import unchanged_required

import os
import sys
import copy
import fnmatch
import re

from docutils.parsers import rst
from sphinx.domains.cpp import DefinitionParser

from breathe.builder import RstBuilder, BuilderFactory
from breathe.finder import FinderFactory, NoMatchesError, MultipleMatchesError
from breathe.parser import DoxygenParserFactory, DoxygenIndexParser
from breathe.renderer.rst.doxygen import DoxygenToRstRendererFactoryCreator
from breathe.renderer.rst.doxygen.domain import DomainHandlerFactoryCreator, CppDomainHelper, CDomainHelper
from breathe.finder.doxygen import DoxygenItemFinderFactoryCreator, ItemMatcherFactory

import docutils.nodes
import sphinx.addnodes

# Somewhat outrageously, reach in and fix a Sphinx regex 
import sphinx.domains.cpp
sphinx.domains.cpp._identifier_re = re.compile(r'(~?\b[a-zA-Z_][a-zA-Z0-9_]*)\b')

class BaseDirective(rst.Directive):

    def __init__(self, builder_factory, finder_factory, matcher_factory, project_info_factory, *args):
        rst.Directive.__init__(self, *args)

        self.builder_factory = builder_factory
        self.finder_factory = finder_factory
        self.matcher_factory = matcher_factory
        self.project_info_factory = project_info_factory


# Directives
# ----------

class DoxygenIndexDirective(BaseDirective):

    required_arguments = 0
    optional_arguments = 2
    option_spec = {
            "path" : unchanged_required,
            "project" : unchanged_required,
            }
    has_content = False

    def run(self):

        project_info = self.project_info_factory.create_project_info(self.options)

        finder = self.finder_factory.create_finder(project_info)

        # try:
        data_object = finder.root()
        # except

        builder = self.builder_factory.create_builder(project_info, self.state.document)
        nodes = builder.build(data_object)

        return nodes


class DoxygenFunctionDirective(BaseDirective):

    required_arguments = 1
    optional_arguments = 1
    option_spec = {
            "path" : unchanged_required,
            "project" : unchanged_required,
            }
    has_content = False

    def run(self):

        try:
            (namespace, function_name) = self.arguments[0].rsplit("::", 1)
        except ValueError:
            (namespace, function_name) = "", self.arguments[0]

        project_info = self.project_info_factory.create_project_info(self.options)

        finder = self.finder_factory.create_finder(project_info)

        matcher_stack = self.matcher_factory.create_matcher_stack(
                {
                    "compound" : self.matcher_factory.create_name_matcher(namespace),
                    "member" : self.matcher_factory.create_name_type_matcher(function_name, "function")
                },
                "member"
            )

        try:
            data_object = finder.find_one(matcher_stack)
        except NoMatchesError, e:
            warning = 'doxygenfunction: Cannot find function "%s%s" in doxygen xml output' % (namespace, function_name)
            return [ docutils.nodes.warning( "", docutils.nodes.paragraph("", "", docutils.nodes.Text(warning))),
                    self.state.document.reporter.warning( warning, line=self.lineno) ]

        builder = self.builder_factory.create_builder(project_info, self.state.document)
        nodes = builder.build(data_object)

        return nodes



class DoxygenBaseDirective(BaseDirective):

    required_arguments = 1
    optional_arguments = 1
    option_spec = {
            "path" : unchanged_required,
            "project" : unchanged_required,
            }
    has_content = False

    def run(self):

        name = self.arguments[0]

        project_info = self.project_info_factory.create_project_info(self.options)

        finder = self.finder_factory.create_finder(project_info)

        matcher_stack = self.create_matcher_stack(name)

        try:
            data_object = finder.find_one(matcher_stack)
        except NoMatchesError, e:
            warning = 'doxygen%s: Cannot find %s "%s" in doxygen xml output' % (self.kind, self.kind, name)
            return [ docutils.nodes.warning( "", docutils.nodes.paragraph("", "", docutils.nodes.Text(warning))),
                    self.state.document.reporter.warning( warning, line=self.lineno) ]

        builder = self.builder_factory.create_builder(project_info, self.state.document)
        nodes = builder.build(data_object)

        return nodes


class DoxygenStructDirective(DoxygenBaseDirective):

    kind = "struct"

    def create_matcher_stack(self, name):

        return self.matcher_factory.create_matcher_stack(
                {
                    "compound" : self.matcher_factory.create_name_type_matcher(name, self.kind)
                },
                "compound"
            )


class DoxygenClassDirective(DoxygenBaseDirective):

    kind = "class"

    def create_matcher_stack(self, name):

        return self.matcher_factory.create_matcher_stack(
                {
                    "compound" : self.matcher_factory.create_name_type_matcher(name, self.kind)
                },
                "compound"
            )


class DoxygenEnumDirective(DoxygenBaseDirective):

    kind = "enum"

    def create_matcher_stack(self, name):

        return self.matcher_factory.create_matcher_stack(
                {
                    "compound" : self.matcher_factory.create_name_matcher(""),
                    "member" : self.matcher_factory.create_name_type_matcher(name, self.kind)
                },
                "member"
            )

class DoxygenTypedefDirective(DoxygenBaseDirective):

    kind = "typedef"

    def create_matcher_stack(self, name):

        return self.matcher_factory.create_matcher_stack(
                {
                    "compound" : self.matcher_factory.create_name_matcher(""),
                    "member" : self.matcher_factory.create_name_type_matcher(name, self.kind)
                },
                "member"
            )



# Setup Administration
# --------------------

class DirectiveContainer(object):

    def __init__(self, directive, builder, finder_factory, matcher_factory, project_info_factory):

        self.directive = directive
        self.builder = builder
        self.finder_factory = finder_factory
        self.matcher_factory = matcher_factory
        self.project_info_factory = project_info_factory

        # Required for sphinx to inspect
        self.required_arguments = directive.required_arguments
        self.optional_arguments = directive.optional_arguments
        self.option_spec = directive.option_spec
        self.has_content = directive.has_content

    def __call__(self, *args):

        return self.directive(self.builder, self.finder_factory, self.matcher_factory, self.project_info_factory, *args)


class ProjectInfo(object):

    def __init__(self, name, path, reference, domain_by_extension, domain_by_file_pattern, match):

        self._name = name
        self._path = path
        self._reference = reference
        self._domain_by_extension = domain_by_extension
        self._domain_by_file_pattern = domain_by_file_pattern
        self._match = match

    def name(self):
        return self._name

    def path(self):
        return self._path

    def reference(self):
        return self._reference

    def domain_for_file(self, file_):

        domain = ""
        extension = file_.split(".")[-1]

        try:
            domain = self._domain_by_extension[extension]
        except KeyError:
            pass

        for pattern, pattern_domain in self._domain_by_file_pattern.items():
            if self._match(file_, pattern):
                domain = pattern_domain

        return domain


class ProjectInfoFactory(object):

    def __init__(self, match):

        self.match = match

        self.projects = {}
        self.default_project = None
        self.domain_by_extension = {}
        self.domain_by_file_pattern = {}

        self.project_count = 0
        self.project_info_store = {}
        

    def update(
            self,
            projects,
            default_project,
            domain_by_extension,
            domain_by_file_pattern,
            ):

        self.projects = projects
        self.default_project = default_project
        self.domain_by_extension = domain_by_extension
        self.domain_by_file_pattern = domain_by_file_pattern

    def default_path(self):

        return self.projects[self.default_project]

    def create_project_info(self, options):

        name = ""
        path = self.default_path()

        if options.has_key("project"):
            try:
                path = self.projects[ options["project"] ]
                name = options["project"]
            except KeyError, e:
                sys.stderr.write(
                        "Unable to find project '%s' in breathe_projects dictionary" % options["project"]
                        )

        if options.has_key("path"):
            path = options["path"]


        try:
            return self.project_info_store[path]
        except KeyError:

            reference = name

            if not name:
                name = "project%s" % self.project_count
                reference = path
                self.project_count += 1

            project_info = ProjectInfo(
                    name,
                    path,
                    reference,
                    self.domain_by_extension,
                    self.domain_by_file_pattern,
                    self.match
                    )

            self.project_info_store[path] = project_info

            return project_info



class DoxygenDirectiveFactory(object):

    directives = {
            "doxygenindex" : DoxygenIndexDirective,
            "doxygenfunction" : DoxygenFunctionDirective,
            "doxygenstruct" : DoxygenStructDirective,
            "doxygenclass" : DoxygenClassDirective,
            "doxygenenum" : DoxygenEnumDirective,
            "doxygentypedef" : DoxygenTypedefDirective,
            }

    def __init__(self, builder_factory, finder_factory, matcher_factory, project_info_factory):
        self.builder_factory = builder_factory
        self.finder_factory = finder_factory
        self.matcher_factory = matcher_factory
        self.project_info_factory = project_info_factory

    def create_index_directive_container(self):
        return self.create_directive_container("doxygenindex")

    def create_function_directive_container(self):
        return self.create_directive_container("doxygenfunction")

    def create_struct_directive_container(self):
        return self.create_directive_container("doxygenstruct")

    def create_enum_directive_container(self):
        return self.create_directive_container("doxygenenum")

    def create_typedef_directive_container(self):
        return self.create_directive_container("doxygentypedef")

    def create_class_directive_container(self):
        return self.create_directive_container("doxygenclass")

    def create_directive_container(self, type_):

        return DirectiveContainer(
                self.directives[type_],
                self.builder_factory,
                self.finder_factory,
                self.matcher_factory,
                self.project_info_factory
                )

    def get_config_values(self, app):

        # All DirectiveContainers maintain references to this project info factory
        # so we can update this to update them
        self.project_info_factory.update(
                app.config.breathe_projects,
                app.config.breathe_default_project,
                app.config.breathe_domain_by_extension,
                app.config.breathe_domain_by_file_pattern,
                )

class NodeFactory(object):

    def __init__(self, *args):

        self.sources = args

    def __getattr__(self, node_name):

        for source in self.sources:
            try:
                return getattr(source, node_name)
            except AttributeError:
                pass

        raise NodeNotFoundError(node_name)


# Setup
# -----

def setup(app):

    parser_factory = DoxygenParserFactory()
    matcher_factory = ItemMatcherFactory()
    item_finder_factory_creator = DoxygenItemFinderFactoryCreator(parser_factory, matcher_factory)
    index_parser = DoxygenIndexParser()
    finder_factory = FinderFactory(index_parser, item_finder_factory_creator)

    node_factory = NodeFactory(docutils.nodes, sphinx.addnodes)
    cpp_domain_helper = CppDomainHelper(DefinitionParser, re.sub)
    c_domain_helper = CDomainHelper()
    domain_helpers = {"c" : c_domain_helper, "cpp" : cpp_domain_helper}
    domain_handler_factory_creator = DomainHandlerFactoryCreator(node_factory, domain_helpers)
    renderer_factory_creator = DoxygenToRstRendererFactoryCreator(node_factory, parser_factory, domain_handler_factory_creator)
    builder_factory = BuilderFactory(RstBuilder, renderer_factory_creator)

    project_info_factory = ProjectInfoFactory(fnmatch.fnmatch)
    directive_factory = DoxygenDirectiveFactory(builder_factory, finder_factory, matcher_factory, project_info_factory)

    app.add_directive(
            "doxygenindex",
            directive_factory.create_index_directive_container(),
            )

    app.add_directive(
            "doxygenfunction",
            directive_factory.create_function_directive_container(),
            )

    app.add_directive(
            "doxygenstruct",
            directive_factory.create_struct_directive_container(),
            )

    app.add_directive(
            "doxygenenum",
            directive_factory.create_enum_directive_container(),
            )

    app.add_directive(
            "doxygentypedef",
            directive_factory.create_typedef_directive_container(),
            )

    app.add_directive(
            "doxygenclass",
            directive_factory.create_class_directive_container(),
            )

    app.add_config_value("breathe_projects", {}, True)
    app.add_config_value("breathe_default_project", "", True)
    app.add_config_value("breathe_domain_by_extension", {}, True)
    app.add_config_value("breathe_domain_by_file_pattern", {}, True)

    app.connect("builder-inited", directive_factory.get_config_values)


