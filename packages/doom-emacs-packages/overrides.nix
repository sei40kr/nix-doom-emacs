{
  lib,
  newScope,
  fetchFromGitHub,
  fetchFromGitLab,
  fetchFromGitea,
  fetchgit,
  melpaBuild,
  elpaBuild,
  writeText,
  emacs,
  trivialBuild,
  lock,
  ocamlPackages,
  git,
}:

let
  generatedPkgs = lib.makeExtensible (
    self:
    (import ./generated.nix {
      inherit
        lib
        newScope
        fetchFromGitHub
        fetchFromGitLab
        fetchFromGitea
        fetchgit
        melpaBuild
        elpaBuild
        writeText
        emacs
        trivialBuild
        ;
      emacsPackages = emacs.pkgs;
    })
  );

  overrides = self: super: {
    evil-easymotion = super.evil-easymotion.overrideAttrs (
      {
        packageRequires ? [ ],
        ...
      }:
      {
        packageRequires = packageRequires ++ [ self.evil ];
      }
    );

    evil-markdown = super.evil-markdown.overrideAttrs (
      {
        packageRequires ? [ ],
        ...
      }:
      {
        packageRequires = packageRequires ++ [
          self.evil
          self.markdown-mode
        ];
      }
    );

    magit = super.magit.overrideAttrs ({ version, ... }: {
      preBuild = ''
        make VERSION="${version}" -C lisp magit-version.el
      '';
    });

    straightBuild =
      { pname, ... }@args:
      self.trivialBuild (
        {
          ename = pname;
          version = "1";
          src = lock pname;
          buildPhase = ":";
        }
        // args
      );

    straight = self.trivialBuild {
      pname = "straight";
      ename = "straight";
      version = super.straight.version;
      src = super.straight.src;
      nativeBuildInputs = [ git ];
    };

    use-package = melpaBuild {
      pname = "use-package";
      version = "20220625.1237";
      commit = "0ad5d9d5d8a61517a207ab04bf69e71c081149eb";

      src = fetchFromGitHub {
        owner = "jwiegley";
        repo = "use-package";
        rev = "0ad5d9d5d8a61517a207ab04bf69e71c081149eb";
        hash = "sha256-nJcSaWcHAanGluVj4rhyHn3jY2i8O8TdpjLHSEgKT4Q=";
      };

      recipe = writeText "recipe" ''
        (use-package :fetcher github :repo "jwiegley/use-package")
      '';

      packageRequires = [ ];
    };

    doom-snippets = self.straightBuild {
      pname = "doom-snippets";
      installPhase = ''
        mkdir -p $out/share/emacs/site-lisp
        cp -r $src/* $out/share/emacs/site-lisp/
      '';
    };

    nose = self.straightBuild {
      pname = "nose";
    };

    org-contrib = self.straightBuild {
      pname = "org-contrib";
      installPhase = ''
        mkdir -p $out/share/emacs/site-lisp
         cp -r lisp/* $out/share/emacs/site-lisp
      '';
    };

    org = self.straightBuild rec {
      pname = "org";
      version = "9.4";
      installPhase = ''
        LISPDIR=$out/share/emacs/site-lisp
        install -d $LISPDIR

        cp -r * $LISPDIR

        cat > $LISPDIR/lisp/org-version.el <<EOF
        (fset 'org-release (lambda () "${version}"))
        (fset 'org-git-version #'ignore)
        (provide 'org-version)
        EOF
      '';
    };

    restart-emacs = super.restart-emacs.overrideAttrs (esuper: {
      patches = [ ../../patches/restart-emacs.patch ];
    });

    revealjs = self.straightBuild {
      pname = "revealjs";

      installPhase = ''
        LISPDIR=$out/share/emacs/site-lisp
        install -d $LISPDIR

        cp -r * $LISPDIR
      '';
    };

    # dune has a nontrivial derivation, which does not buildable from the melpa
    # wrapper falling back to the one in nixpkgs
    dune = ocamlPackages.dune_2.overrideAttrs (old: {
      # Emacs derivations require an ename attribute
      ename = old.pname;

      # Need to adjust paths here match what doom expects
      postInstall = ''
        mkdir -p $out/share/emacs/site-lisp/editor-integration
        ln -snf $out/share/emacs/site-lisp $out/share/emacs/site-lisp/editor-integration/emacs
      '';
    });
  };
in
generatedPkgs.extend overrides
