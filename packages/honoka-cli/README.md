# honoka-cli

The `honoka` command: look at an app without serving it.

```sh
honoka routes main:app      # list the routes
honoka check main:app       # find mistakes in them
```

`main:app` is `module:attribute` of a `honoka.App` (the attribute defaults to `app`), imported with
the current directory on the path. It must be an `App` object: a factory function such as
`create_app` is not called, so point it at a module that builds one.

```text
$ honoka routes main:app
GET   /users/{id:int}  get_user
POST  /users           create_user

$ honoka check main:app
✗ GET /users/{id:int}: c.req.param('user_id') is not a path parameter of this route
! POST /users: create_user has no return annotation
1 error, 1 warning
```

`routes` prints method, path and handler name. `check` compiles the routes (so a duplicate or
unsupported pattern is reported) and reads each handler's source for mistakes that would otherwise
only show up on a request:

- **error**: `c.req.param("x")` where the route has no parameter `x`
- **error**: a path parameter the handler never reads (skipped when it reads `c.req.params`
  or a name that is not a literal, or the route has a `param` validator)
- **error**: `c.req.valid(T)` where no validator on the route produces a `T`
- **warning**: a handler without a return annotation

The exit status is 1 when there is an error, 2 when the app cannot be imported, so it fits in CI:
`honoka check main:app`.

The source is read with `inspect.getsource`; a handler whose source is not available (a lambda in
the REPL, a compiled function) only gets the annotation check.
