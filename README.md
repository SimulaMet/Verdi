# Verdi website

Source of https://simulamet.github.io/Verdi/. This branch is separate from
`main` (no shared history) so the project branch holds no site code.

- `build.py` renders a landing page plus the guides from `main`'s `docs/*.md`,
  with SEO metadata, `sitemap.xml` and `robots.txt`.
- `.github/workflows/pages.yml` deploys on every push to this branch.

After docs change on `main`, redeploy:

```bash
gh workflow run pages.yml --ref website --repo SimulaMet/Verdi
```

Local preview:

```bash
git clone -b main https://github.com/SimulaMet/Verdi.git main
pip install markdown && python build.py && python -m http.server -d _site
```
