Bugger
======

So, I needed a tool for doing arbitrary tests of my software...something other than unit testing.

I didn't find one so I made this one. It can do all the things that I want since it simply executes
programs and checks for certain success criteria. If those aren't met, the test failed. Pretty simple.

Usage
-----

Make a symlink pointing to the `bugger.py` script:

```
$ sudo ln -s $(pwd)/bugger.py /usr/local/bin/bugger
```

Now create a top level test script called `bugger.json` in your project folder (more on that later)
and you should be ready to do some testing. Just run `bugger` and it will look for a file called
`bugger.json` in the current directory or you can specify the filename as an argument.

I usually execute `bugger` every time I save my work using a command like this:

```
$ while true; do find src -type f -o -type d | entr bugger ; done
```

So that is how to install and run, but how do you actually write tests? Read on.

Writing `bugger.json`
---------------------

You write your tests in a JSON file called `bugger.json` (bugger will look for this
but really you can call it anything you want). It contains one JSON object with
three keys: `environment`, `settings` and `command_groups` such as:

```json
{
    "environment": {
        "PROJECT_PATH": "${BUGGER_DIR}",
        "BUILD_PATH": "${PROJECT_PATH}/build"
    },
    "settings": {
        "timeout": 5
    },
    "command_groups": {
        "Build project": [
            {
                "name": "Delete previous build",
                "system": "rm -rf ${BUILD_PATH}/*"
            },
            {
                "name": "Make build directory",
                "system": "test -d ${BUILD_PATH} || mkdir ${BUILD_PATH}"
            },
            {
                "name": "Build makefile",
                "system": "cmake ${PROJECT_PATH}",
                "settings": {
                    "chdir": "${BUILD_PATH}",
                    "exit-on-fail": true
                }
            },
            {
                "name": "Build",
                "system": "make -j4",
                "settings": {
                    "timeout": 20,
                    "chdir": "${BUILD_PATH}",
                    "exit-on-fail": true
                }
            }
        ]
    }
}
```

All (well, most) strings in the file can be subject to a set of expansion rules
before being used. This means you can use environment variables or the output of
commands inside strings. More on that later.

## `environment`

This block sets environment variables that will be used thruout the rest of the
file. These can be added to or overridden in actual tests.

Bugger creates two variables for you, `BUGGER_FILE` which points to the top
level test file and `BUGGER_DIR` which points to the directorie which contins
it.

## `settings`

The settings tells Bugger how to behave. These can be set in the top level
object or in specific tests which overrides the global ones. Each individual
setting will be described next.

### `timeout`

Defaults to 10 seconds. If a test takes longer than this, it will be killed and
the test failed.

### `exit-on-fail`

Defaults to `false`. If a test fails it is recorded and the next will be
executed unless this is set to `true` in which case all remaining tests will be
skipped. Usually you don't want this but sometimes you do, for instance if your
compile step failed there is really no reason to continue testing.

### `chdir`

Change to this directory before executing the test.

### `expected-exit-code`

Defaults to 0. The test fails if the command did not exit cleanly with this
exit code.

### `animation`

Defaults to `true`. Bugger will show an animation while running a test. Set
this to `false` to prevent it.

### `enable-collapse`

Defaults to `true`. If you have too many tests to show them all in your
terminal you can collapse the command groups to only show the currently
executing one.

### `save-output`

After having run Bugger can save a series of log files into this directory.
It will create subdirectories for each command group and each individual test.

## `command_groups`

This is a JSON object which maps group names to an array of individual tests.

When you have many tests it is nice to split them into groups by what they do.

Like the build group shown above, which I have in all my compiled projects.

Then come a group for each logical feature testing it in different scenarios.

But how do you actually write a test?

### Writing a test

Each test is a JSON object with a mandatory name and either a `system` or `exec`
member. Each member is described next.

#### `name`

This is mandatory. It is simply a string stating what the test is about.

#### `system`

Command executed through `bash -c` which is the actual test.

This or `exec` must be specified.

#### `exec`

Alternative to `system`. Executed through the `execve` system call.
Arguments are specified using the `arguments` member.

#### `arguments`

Arguments to the test command specified in the `exec` member.

#### `settings`

Overrides settings from the root JSON object.

#### `environment`

Overrides environment variables from the root JSON object.

#### `stdout-to-env`

Names an environment variable which should receive the output of this command.

The variable will be available to all remaining tests. This can for example be
used to record a session id for a test client created early in the process so
that the same client can be referenced later.

#### `output-contains`

This can be either a string or a list of strings. All strings must be found
somewhere in the command output for the command to be considered successful.

#### `!output-contains`

This can be either a string or a list of strings. None of the strings may be found
anywhere in the command output for the command to be considered successful.

#### `output-matches`

Output must match the specified string exactly.

#### `success-command`

Run this through `bash -c` to check if the test was successful. Success or
failure is indicated by the exit code. This can be used for testing some side
effect that cannot be detected through its output such as the presence or
absence of a file.

#### Success or failure

A command fails in the following circumstances:

* Terminates with an exit code different from what was expected
* Killed with a signal
* Times out
* Output not meeting expectations
* Success checking command indicated failure

The expected exit code can be overridden by using the `expected-exit-code`
setting.

Command output can be matched against a set of expectations which can also fail
it. These are specified above.

#### Included test commands

Instead of a command group being a list of tests it can be a JSON object
containing a mandatory "include" member and optional "environment" and "setting"
members. This can be used to keep the test file short and also to reuse tests
with different environment variables and settings like this:

```json
{
    "command_groups": {
        "Build with gcc": {
            "include": "tests/build.json",
            "environment": {
                "COMPILER": "gcc"
            }
        },
        "Build with clang": {
            "include": "tests/build.json",
            "environment": {
                "COMPILER": "clang"
            }
        }
    }
}
```

#### Expansion rules

You can use environment variables and the output of commands inside most of the
strings used in your `bugger.json` files. These are expanded much like in shell
scripts.

##### Command output

Output from commands can be expanded like this: `"output-matches": "$(echo -n hello | md5sum)"`.

Note that trailing newlines are also included but you can fix this by going through an environment variable and using a filter. Read the next section.

##### Environment variables

Environment variables are used like this: `"${COMPILER} -o thing thing.c"`.

Like in bash you can have default values like this: `"${COMPILER:-gcc} -o thing thing.c"`.

Environment variables can be filtered like this:

```
{
    "name": "Use a filter",
    "environment": {
        "MD5SUM": "$(echo -n hello | md5sum | awk '{print $1}')"
    },
    "system": "echo \"The MD5 sum is: ${MD5SUM|rstrip|upper}.\""
}
```

The above will create an environment variable with the value `"5d41402abc4b2a76b9719d911017c592\n"`.

Then when using it it will first have trailing whitespace stripped and then be uppercased.

All python string methods with no arguments can be used as a filter.
