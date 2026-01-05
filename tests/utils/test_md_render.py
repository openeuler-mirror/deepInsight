"""Testcase for module `deepinsight.utils.md_render`"""
from unittest import TestCase

from deepinsight.utils.md_render import fix_md_list  # noqa: for unittest


MD_LIST_IN_A = """# title
- This is the first list
A common line

- A list with prefix empty line but without postfix empty line
```python```
Should no empty before this line
- This is another list
```yaml~~~
- This can't be processed
```
```markdown
~~~
- This also can't be processed
```
`````md
- no processed
````
- also no processed
`````
- This is a top level list
  - This is a second level list
A common line.
1. This is another list
```cpp
// broken code block. It cannot raises.
"""
MD_LIST_OUT_A = """# title

- This is the first list

A common line

- A list with prefix empty line but without postfix empty line

```python```
Should no empty before this line

- This is another list

```yaml~~~
- This can't be processed
```
```markdown
~~~
- This also can't be processed
```
`````md
- no processed
````
- also no processed
`````

- This is a top level list
  - This is a second level list

A common line.

1. This is another list

```cpp
// broken code block. It cannot raises.
"""

MD_LIST_IN_OUT_B = """1. This is a list\n"""


class TestMarkdownFix(TestCase):
    def test_list_fix(self):
        self.assertEqual(MD_LIST_IN_OUT_B, fix_md_list(MD_LIST_IN_OUT_B))
        self.assertEqual(MD_LIST_OUT_A, fix_md_list(MD_LIST_IN_A))
