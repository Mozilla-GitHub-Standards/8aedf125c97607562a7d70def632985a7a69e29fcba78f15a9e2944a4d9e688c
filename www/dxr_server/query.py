import utils, cgi, codecs

# Register filters by adding them to this list.
filters = []

# TODO
#   - Special argument files-only to just search for file names
#   - If no plugin returns an extents query, don't fetch content


import re

#TODO _parameters should be extracted from filters (possible if filters are defined first)
# List of parameters to isolate in the search query, ie. path:mypath
_parameters = ["path", "ext", "type", "type-ref", "function", "function-ref",
"var", "var-ref", "macro", "macro-ref", "callers", "called-by", "warning",
"bases", "derived", "member"]

_parameters += ["-" + param for param in _parameters] + ["+" + param for param
    in _parameters] + ["-+" + param for param in _parameters] + ["+-" + param for param in _parameters]

#TODO Support negation of phrases, support phrases as args to params, ie. path:"my path", or warning:"..."


# Pattern recognizing a parameter and a argument, a phrase or a keyword
_pat = "((?P<param>%s):(?P<arg>[^ ]+))|(\"(?P<phrase>[^\"]+)\")|(?P<keyword>[^ \"]?[^ \"-]+)"
_pat = re.compile(_pat % "|".join([re.escape(p) for p in _parameters]))

class Query:
  """ Query object, constructor will parse any search query """
  def __init__(self, querystr):
    self.params = {}
    for param in _parameters:
      self.params[param] = []
    self.notwords = []
    self.keywords = []
    self.phrases = []
    # We basically iterate over the set of matches left to right
    for token in (match.groupdict() for match in _pat.finditer(querystr)):
      if token["param"] and token["arg"]:
        self.params[token["param"]].append(token["arg"])
      if token["phrase"]:
        self.phrases.append(token["phrase"])
      if token["keyword"]:
        if token["keyword"].startswith("-"):
          self.notwords.append(token["keyword"][1:])
        else:
          self.keywords.append(token["keyword"])


#TODO Use named place holders in filters, this would make the filters easier to write

# Fetch results using a query,
# See: queryparser.py for details in query specification
def fetch_results(conn, query,
                  offset = 0, limit = 100,
                  markup = "<b>", markdown = "</b>"):
  sql = "SELECT files.path, fts.content, files.ID FROM fts, files WHERE %s LIMIT ? OFFSET ?"
  conditions = " files.ID = fts.rowid "
  arguments = []

  has_extents = False
  for f in filters:
    for conds, args, exts in f.filter(query):
      has_extents = exts or has_extents
      conditions += " AND " + conds
      arguments += args
  sql %= conditions
  arguments += [limit, offset]


  #TODO Actually do something with the has_extents, ie. don't fetch contents

  utils.log(sql)
  utils.log(arguments)

  # Make a simple decoder for decoding unicode
  # Note that we need to operate in ascii inorder to handle
  # compiler offsets
  decoder = codecs.getdecoder("utf-8")
  def d(string):
    return decoder(string, errors="replace")[0]

  for path, content, fileid in conn.execute(sql, arguments):
    elist = []
    for f in filters:
      for e in f.extents(conn, query, fileid):
        elist.append(e)
    offsets = list(merge_extents(*elist))

    lines = []
    line_number = 1
    last_pos = 0

    for i in xrange(0, len(offsets)):
      # TODO keylist should infact have information about which extent of the
      # search query caused this hit, we should highlight this extent
      # (Note. Query object still doesn't provide support for offering this
      #  extent, and this needs to be supported and used in filters).
      estart, eend, keylist = offsets[i]

      # Skip if we didn't get a new line
      line_diff = content.count("\n", last_pos, estart)
      if line_diff == 0 and last_pos > 0:
        continue 
      line_number += line_diff
      last_pos = estart

      # Find newline before and after offset
      end       = content.find("\n", estart)
      start     = max(content.rfind("\n", 0, end), 0)
      src_line  = content[start:end]

      # Part from line break to start is escape and outputted
      out_line = cgi.escape(d(src_line[1:estart - start]))

      # Add some markup to highlight hits
      while content.count("\n", last_pos, estart) == 0:
        mstart = estart - start
        mend   = eend - start
        # Output markup and line segment
        out_line += markup + cgi.escape(d(src_line[mstart:mend])) + markdown
        i += 1
        if i >= len(offsets):
          break
        estart, end, keylist = offsets[i]

      # Output the rest of the line when theres no more offsets
      # Notice that the while loop always goes atleast once
      out_line += cgi.escape(d(src_line[mend:]))

      lines.append((line_number, out_line))
    # Return result
    yield path, lines


def like_escape(val):
  """ Escape for usage in as argument to the LIKE operator """
  return val.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")


class genWrap:
  """ Auxiliary class for wrapping a generator and make it nicer """
  def __init__(self, gen):
    self.gen = gen
    self.value = None
  def next(self):
    try:
      self.value = self.gen.next()
      return True
    except StopIteration:
      self.value = None
      return False

def merge_extents(*elist):
  """
    Take a list of extents generators and merge them into one stream of extents
    overlapping extents will be split in two, this means that they will start
    and stop at same location.
    Here we assume that each extent is a triple as follows:
      (start, end, keyset)

    Where keyset is a list of something that should be applied to the extent
    between start and end.
  """
  elist = [genWrap(e) for e in elist]
  elist = [e for e in elist if e.next()]
  while len(elist) > 0:
    start = min((e.value[0] for e in elist))
    end = min((e.value[1] for e in elist if e.value[0] == start))
    keylist = []
    for e in (e for e in elist if e.value[0] == start):
      for k in e.value[2]:
        if k not in keylist:
          keylist.append(k)
      e.value = (end, e.value[1], e.value[2])
    yield start, end, keylist
    elist = [e for e in elist if e.value[0] < e.value[1] or e.next()]


class SearchFilter:
  """ Base class for all search filters, plugins subclasses this class and
      registers an instance of them calling register_filter
  """
  def __init__(self):
    """ Initialize the filter, self.params is the keywords used by this filter,
        Fail to declare keywords and the query-parser will not parse them!
    """
    self.params = []
  def filter(self, query):
    """ Given a query yield tuples of sql conditions, list of arguments and
        boolean True if this filter offer extents for results,
        Note the sql conditions must be string and condition on files.ID
    """
    pass
  def extents(self, conn, query, fileid):
    """ Given a connection, query and a file id yield a ordered lists of extents to highlight """
    pass

class FTSSearchFilter(SearchFilter):
  """ Full Text Search filter """
  def __init__(self):
    SearchFilter.__init__(self)
  def filter(self, query):
    if len(query.keywords) or len(query.phrases):
      q = " ".join(query.keywords + ["-%s" % w for w in query.notwords] + ['"%s"' % phrase for phrase in query.phrases])
      yield "fts.content MATCH ?", [q], True
    elif len(query.notwords):
      q = " ".join(query.notwords)
      yield "files.ID NOT IN (SELECT n.rowid FROM fts as n WHERE n.content MATCH ?)", [q], False
  def extents(self, conn, query, fileid):
    if len(query.keywords) or len(query.phrases):
      def builder():
        sql = "SELECT offsets(fts) FROM fts WHERE fts.content MATCH ? AND fts.rowid = ?"
        q = " ".join(query.keywords + ["-%s" % w for w in query.notwords] + ['"%s"' % phrase for phrase in query.phrases])
        offsets = conn.execute(sql, [q, fileid]).fetchone()
        offsets = offsets[0].split()
        offsets = [offsets[i:i+4] for i in xrange(0, len(offsets), 4)]
        for col, term, start, size in offsets:
          yield (int(start), int(start) + int(size), [])
      yield builder()

class SimpleFilter(SearchFilter):
  """ Search filter for limited results.
      This filter take 5 parameters, defined as follows:
        param           Search parameter from query
        filter_sql      Sql condition for limited using argument to param
        neg_filter_sql  Sql condition for limited using argument to param negated.
        ext_sql         Sql statement fetch an ordered list of extents, given
                        file-id and argument to param as parameters.
                        (None if not applicable)
        formatter       Function/lambda expression for formatting the argument
  """
  def __init__(self, param, filter_sql, neg_filter_sql, ext_sql, formatter):
    SearchFilter.__init__(self)
    self.param = param
    self.params += (param, "-%s" % param)
    self.filter_sql = filter_sql
    self.neg_filter_sql = neg_filter_sql
    self.ext_sql = ext_sql
    self.formatter = formatter
  def filter(self, query):
    for arg in query.params[self.param]:
      yield self.filter_sql, self.formatter(arg), self.ext_sql is not None
    for arg in query.params["-%s" % self.param]:
      yield self.neg_filter_sql, self.formatter(arg), False
  def extents(self, conn, query, fileid):
    if self.ext_sql:
      for arg in query.params[self.param]:
        for start, end in conn.execute(self.ext_sql, [fileid] + self.formatter(arg)):
          yield start, end, []

class ExistsLikeFilter(SearchFilter):
  """ Search filter for asking of something LIKE this EXISTS,
      This filter takes 5 parameters, param is the search query parameter,
      "-" + param is a assumed to be the negated search filter.
      The filter_sql must be an (SELECT 1 FROM ... WHERE ... %s ...), sql condition on files.ID,
      s.t. replacing %s with "qual_name = ?" or "like_name LIKE %?%" where ? is arg given to param
      in search query, and prefixing with EXISTS or NOT EXISTS will yield search
      results as desired :)
      (BTW, did I mention that 'as desired' is awesome way of writing correct specifications)
      ext_sql, must be an sql statement for a list of extent start and end,
      given arguments (file_id, %arg%), where arg is the argument given to
      param. Again %s will be replaced with " = ?" or "LIKE %?%" depending on
      whether or not param is prefixed +
  """
  def __init__(self, param, filter_sql, ext_sql, qual_name, like_name):
    SearchFilter.__init__(self)
    self.param = param
    self.params += (param, "+" + param, "-" + param, "+-" + param, "-+" + param)
    self.filter_sql = filter_sql
    self.ext_sql = ext_sql
    self.qual_expr = " %s = ? " % qual_name
    self.like_expr = """ %s LIKE ? ESCAPE "\\" """ % like_name
  def filter(self, query):
    for arg in query.params[self.param]:
      yield (
              "EXISTS (%s)" % (self.filter_sql % self.like_expr),
              ['%' + like_escape(arg) + '%'],
              self.ext_sql is not None
            )
    for arg in query.params["+" + self.param]:
      yield (
              "EXISTS (%s)" % (self.filter_sql % self.qual_expr),
              [arg],
              self.ext_sql is not None
            )
    for arg in query.params["+-" + self.param] + query.params["-+" + self.param]:
      yield (
              "NOT EXISTS (%s)" % (self.filter_sql % self.qual_expr),
              [arg],
              False
            )
    for arg in query.params["-" + self.param]:
      yield (
              "NOT EXISTS (%s)" % (self.filter_sql % self.like_expr),
              ['%' + like_escape(arg) + '%'],
              False
            )
  def extents(self, conn, query, fileid):
    if self.ext_sql:
      for arg in query.params[self.param]:
        params = [fileid, '%' + like_escape(arg) + '%']
        def builder():
          sql = self.ext_sql % self.like_expr
          for start, end in conn.execute(sql, params):
            # Apparently sometime, None can occur in the database
            if start and end:
              yield (start, end,[])
        yield builder()
      for arg in query.params["+" + self.param]:
        params = [fileid, arg]
        def builder():
          sql = self.ext_sql % self.qual_expr
          for start, end in conn.execute(sql, params):
            # Apparently sometime, None can occur in the database
            if start and end:
              yield (start, end,[])
        yield builder()

# Full Text Search filter
filters.append(FTSSearchFilter())

# path filter
filters.append(SimpleFilter(
    param             = "path",
    filter_sql        = """files.path LIKE ? ESCAPE "\\" """,
    neg_filter_sql    = """files.path NOT LIKE ? ESCAPE "\\" """,
    ext_sql           = None,
    formatter         = lambda arg: ['%' + like_escape(arg) + '%']
))

# ext filter
filters.append(SimpleFilter(
    param             = "ext",
    filter_sql        = """files.path LIKE ? ESCAPE "\\" """,
    neg_filter_sql    = """files.path NOT LIKE ? ESCAPE "\\" """,
    ext_sql           = None,
    formatter         = lambda arg: ['%' + like_escape(arg)]
))


# type filter
filters.append(ExistsLikeFilter(
    param         = "type",
    filter_sql    = """SELECT 1 FROM types
                       WHERE %s
                         AND types.file_id = files.ID
                    """,
    ext_sql       = """SELECT types.extent_start, types.extent_end FROM types
                       WHERE types.file_id = ?
                         AND %s
                       ORDER BY types.extent_start
                    """,
    like_name     = "types.tname",
    qual_name     = "types.tqualname"
))


# type-ref filter
filters.append(ExistsLikeFilter(
    param         = "type-ref",
    filter_sql    = """SELECT 1 FROM types, refs
                       WHERE %s
                         AND types.tid = refs.refid AND refs.file_id = files.ID
                    """,
    ext_sql       = """SELECT refs.extent_start, refs.extent_end FROM refs
                       WHERE refs.file_id = ?
                         AND EXISTS (SELECT 1 FROM types
                                     WHERE %s
                                       AND types.tid = refs.refid)
                       ORDER BY refs.extent_start
                    """,
    like_name     = "types.tname",
    qual_name     = "types.tqualname"
))

# function filter
filters.append(ExistsLikeFilter(
    param         = "function",
    filter_sql    = """SELECT 1 FROM functions
                       WHERE %s
                         AND functions.file_id = files.ID
                    """,
    ext_sql       = """SELECT functions.extent_start, functions.extent_end FROM functions
                       WHERE functions.file_id = ?
                         AND %s
                       ORDER BY functions.extent_start
                    """,
    like_name     = "functions.fname",
    qual_name     = "functions.fqualname"
))


# function-ref filter
filters.append(ExistsLikeFilter(
    param         = "function-ref",
    filter_sql    = """SELECT 1 FROM functions, refs
                       WHERE %s
                         AND functions.funcid = refs.refid AND refs.file_id = files.ID
                    """,
    ext_sql       = """SELECT refs.extent_start, refs.extent_end FROM refs
                       WHERE refs.file_id = ?
                         AND EXISTS (SELECT 1 FROM functions
                                     WHERE %s
                                       AND functions.funcid = refs.refid)
                       ORDER BY refs.extent_start
                    """,
    like_name     = "functions.fname",
    qual_name     = "functions.fqualname"
))


# var filter
filters.append(ExistsLikeFilter(
    param         = "var",
    filter_sql    = """SELECT 1 FROM variables
                       WHERE %s
                         AND variables.file_id = files.ID
                    """,
    ext_sql       = """SELECT variables.extent_start, variables.extent_end FROM variables
                       WHERE variables.file_id = ?
                         AND %s
                       ORDER BY variables.extent_start
                    """,
    like_name     = "variables.vname",
    qual_name     = "variables.vname"
))


# var-ref filter
filters.append(ExistsLikeFilter(
    param         = "var-ref",
    filter_sql    = """SELECT 1 FROM variables, refs
                       WHERE %s
                         AND variables.varid = refs.refid AND refs.file_id = files.ID
                    """,
    ext_sql       = """SELECT refs.extent_start, refs.extent_end FROM refs
                       WHERE refs.file_id = ?
                         AND EXISTS (SELECT 1 FROM variables
                                     WHERE %s
                                       AND variables.varid = refs.refid)
                       ORDER BY refs.extent_start
                    """,
    like_name     = "variables.vname",
    qual_name     = "variables.vname"
))


# macro filter
filters.append(ExistsLikeFilter(
    param         = "macro",
    filter_sql    = """SELECT 1 FROM macros
                       WHERE %s
                         AND macros.file_id = files.ID
                    """,
    ext_sql       = None, #TODO Add extent_start, extent_end to macros table!
    like_name     = "macros.macroname",
    qual_name     = "macros.macroname"
))


# macro-ref filter
filters.append(ExistsLikeFilter(
    param         = "macro-ref",
    filter_sql    = """SELECT 1 FROM macros, refs
                       WHERE %s
                         AND macros.macroid = refs.refid AND refs.file_id = files.ID
                    """,
    ext_sql       = """SELECT refs.extent_start, refs.extent_end FROM refs
                       WHERE refs.file_id = ?
                         AND EXISTS (SELECT 1 FROM macros
                                     WHERE %s
                                       AND macros.macroid = refs.refid)
                       ORDER BY refs.extent_start
                    """,
    like_name     = "macros.macroname",
    qual_name     = "macros.macroname"
))


# callers filter
filters.append(ExistsLikeFilter(
    param         = "callers",
    filter_sql    = """SELECT 1 FROM functions as caller, functions as target, callers
                       WHERE %s
                         AND (  ( callers.targetid = target.funcid)
                               OR EXISTS ( SELECT 1 FROM targets
                                           WHERE targets.funcid = target.funcid
                                             AND targets.targetid = callers.targetid
                                         )
                             )
                         AND callers.callerid = caller.funcid
                         AND caller.file_id = files.ID
                    """,
    ext_sql       = """SELECT functions.extent_start, functions.extent_end FROM functions
                       WHERE functions.file_id = ?
                         AND EXISTS (SELECT 1 FROM functions as target, callers
                                      WHERE %s
                                        AND (  ( callers.targetid = target.funcid)
                                              OR EXISTS ( SELECT 1 FROM targets
                                                           WHERE targets.funcid = target.funcid
                                                             AND targets.targetid = callers.targetid
                                                             AND callers.callerid = target.funcid
                                              )
                                            )
                                        AND callers.callerid = functions.funcid
                                    )
                       ORDER BY functions.extent_start
                    """,
    like_name     = "target.fname",
    qual_name     = "target.fqualname"
))

# called-by filter
filters.append(ExistsLikeFilter(
    param         = "called-by",
    filter_sql    = """SELECT 1 FROM functions as target, functions as caller, callers
                        WHERE %s
                          AND callers.callerid = caller.funcid
                          AND (  (callers.targetid = target.funcid)
                                OR EXISTS (SELECT 1 FROM targets
                                            WHERE targets.funcid = target.funcid
                                              AND targets.targetid = callers.targetid
                                          )
                              )
                          AND target.file_id = files.ID
                    """,
    ext_sql       = """SELECT functions.extent_start, functions.extent_end FROM functions
                       WHERE functions.file_id = ?
                         AND EXISTS (SELECT 1 FROM functions as caller, callers
                                      WHERE %s
                                        AND caller.funcid = callers.callerid
                                        AND (   (callers.targetid = functions.funcid)
                                             OR EXISTS (SELECT 1 FROM targets
                                                         WHERE targets.funcid = functions.funcid
                                                           AND targets.targetid = callers.targetid
                                                       )
                                            )
                                    )
                       ORDER BY functions.extent_start
                    """,
    like_name     = "caller.fname",
    qual_name     = "caller.fqualname"
))

#warning filter
filters.append(ExistsLikeFilter(
    param         = "warning",
    filter_sql    = """SELECT 1 FROM warnings
                        WHERE %s
                          AND warnings.file_id = files.ID """,
    ext_sql       = None, #TODO Add extent_start, end to warnings table
    like_name     = "warnings.wmsg",
    qual_name     = "warnings.wmsg" 
))


# bases filter
filters.append(ExistsLikeFilter(
    param         = "bases",
    filter_sql    = """SELECT 1 FROM types as base, impl, types
                        WHERE %s
                          AND impl.tbase = base.tid
                          AND impl.tderived = types.tid
                          AND base.file_id = files.ID""",
    ext_sql       = """SELECT base.extent_start, base.extent_end
                        FROM types as base
                       WHERE base.file_id = ?
                         AND EXISTS (SELECT 1 FROM impl, types
                                     WHERE impl.tbase = base.tid
                                       AND impl.tderived = types.tid
                                       AND %s
                                    )
                    """,
    like_name     = "types.tname",
    qual_name     = "types.tqualname"
))


# derived filter
filters.append(ExistsLikeFilter(
    param         = "derived",
    filter_sql    = """SELECT 1 FROM types as sub, impl, types
                        WHERE %s
                          AND impl.tbase = types.tid
                          AND impl.tderived = sub.tid
                          AND sub.file_id = files.ID""",
    ext_sql       = """SELECT sub.extent_start, sub.extent_end
                        FROM types as sub
                       WHERE sub.file_id = ?
                         AND EXISTS (SELECT 1 FROM impl, types
                                     WHERE impl.tbase = types.tid
                                       AND impl.tderived = sub.tid
                                       AND %s
                                    )
                    """,
    like_name     = "types.tname",
    qual_name     = "types.tqualname"
))


# member filter
filters.append(ExistsLikeFilter(
    param         = "member",
    filter_sql    = """SELECT 1 FROM types as type, (
                             SELECT scopeid, file_id FROM types
                       UNION SELECT scopeid, file_id FROM functions
                       UNION SELECT scopeid, file_id FROM variables
                       ) as mem
                        WHERE %s
                          AND mem.scopeid = type.tid AND mem.file_id = files.ID
                    """,
    ext_sql       = """ SELECT extent_start, extent_end
                          FROM (
                             SELECT extent_start, extent_end, scopeid, file_id FROM types
                       UNION SELECT extent_start, extent_end, scopeid, file_id FROM functions
                       UNION SELECT extent_start, extent_end, scopeid, file_id FROM variables
                       ) as mem WHERE mem.file_id = ?
                                  AND EXISTS ( SELECT 1 FROM types as type
                                                WHERE %s
                                                  AND type.tid = mem.scopeid)
                       ORDER BY mem.extent_start
                    """,
    like_name     = "type.tname",
    qual_name     = "type.tqualname"
))

#TODO typedef filter