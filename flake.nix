{
  description = "Swisstag: Automated Music Tagger";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        # 1. Define lyricsgenius
        lyricsgenius = pkgs.python3Packages.buildPythonPackage rec {
          pname = "lyricsgenius";
          version = "3.7.5";
          pyproject = true;
          src = pkgs.python3Packages.fetchPypi {
            inherit pname version;
            # fake sha
            sha256 = "sha256-xPEMFPeYBFXGXIfQz9EIa+CWba7K+ZaTAy02tlo4qhY=";
          };
          doCheck = false;
          build-system = with pkgs.python3Packages; [ setuptools hatchling ];
          propagatedBuildInputs = with pkgs.python3Packages; [ requests beautifulsoup4 hatchling ];
        };

        # 2. Define syncedlyrics (Manually)
        syncedlyrics = pkgs.python3Packages.buildPythonPackage rec {
          pname = "syncedlyrics";
          version = "1.0.0"; # Update if needed
          pyproject = true;

          src = pkgs.python3Packages.fetchPypi {
            inherit pname version;
            # RUN 'nix build', COPY THE ERROR HASH, AND PASTE IT HERE:
            sha256 = "sha256-JrwIR7s1tYAyS3eYCsmxzkHJz/7WOe7rkDABfhumuZE="; 
          };

          doCheck = false;
          build-system = with pkgs.python3Packages; [ poetry-core ]; # Usually poetry or setuptools
          # rapidfuzz is a key dependency for syncedlyrics
          propagatedBuildInputs = with pkgs.python3Packages; [ requests beautifulsoup4 rapidfuzz ];
        };

        pythonEnv = pkgs.python3.withPackages (ps: with ps; [
          mutagen
          musicbrainzngs
          thefuzz
          requests
          unidecode
          pillow
          beautifulsoup4
          rapidfuzz      # Required by syncedlyrics
          syncedlyrics 
          lyricsgenius
        ]);

      in
      {
        packages.default = pkgs.stdenv.mkDerivation {
          name = "swisstag";
          src = ./.;
          
          # We use makeWrapper to ensure fpcalc is found in PATH
          nativeBuildInputs = [ pkgs.makeWrapper ];
          propagatedBuildInputs = [ pythonEnv pkgs.chromaprint ];
          
          installPhase = ''
            mkdir -p $out/bin
            cp swisstag.py $out/bin/swisstag
            chmod +x $out/bin/swisstag
            
            # Man Page
            mkdir -p $out/share/man/man1
            cp swisstag.1 $out/share/man/man1/swisstag.1
          '';
          
          postFixup = ''
            # 1. Fix python interpreter path
            sed -i '1s|^#!/usr/bin/env python3|#!${pythonEnv}/bin/python3|' $out/bin/swisstag
            
            # 2. Wrap the binary to include fpcalc (chromaprint) in PATH
            wrapProgram $out/bin/swisstag \
              --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.chromaprint ]}
          '';

          meta = with pkgs.lib; {
            description = "Automated music tagger using Genius and MusicBrainz";
            homepage = "https://github.com/doromiert/swisstag";
            license = licenses.gpl3;
            platforms = platforms.all;
          };
        };

        packages.withConfig = configFile: pkgs.stdenv.mkDerivation rec {
          name = "swisstag-with-config";
          src = ./.;
          nativeBuildInputs = [ pkgs.makeWrapper ];
          propagatedBuildInputs = [ pythonEnv pkgs.chromaprint ];

          installPhase = ''
            mkdir -p $out/bin
            cp swisstag.py $out/bin/swisstag
            chmod +x $out/bin/swisstag
            mkdir -p $out/share/man/man1
            cp swisstag.1 $out/share/man/man1/swisstag.1
            mkdir -p $out/etc/swisstag
            cp ${configFile} $out/etc/swisstag/config.json
          '';

          postFixup = ''
            sed -i '1s|^#!/usr/bin/env python3|#!${pythonEnv}/bin/python3|' $out/bin/swisstag
            wrapProgram $out/bin/swisstag --set SWISSTAG_CONFIG $out/etc/swisstag/config.json \
              --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.chromaprint ]}
          '';

        };

        apps.default = flake-utils.lib.mkApp {
          drv = self.packages.${system}.default;
        };

        devShells.default = pkgs.mkShell {
          buildInputs = [ pythonEnv pkgs.chromaprint ];
        };
      }
    );
}
