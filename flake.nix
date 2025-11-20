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

        # Define lyricsgenius manually since it's not in nixpkgs
        lyricsgenius = pkgs.python3Packages.buildPythonPackage rec {
          pname = "lyricsgenius";
          version = "3.0.1";
          
          # Fix for Python 3.13 / Modern Nixpkgs: Enable pyproject format
          pyproject = true;

          src = pkgs.python3Packages.fetchPypi {
            inherit pname version;
            # Use lib.fakeSha256 to force Nix to tell us the actual hash
            sha256 = "sha256-g671X/yguOppZRxLFEaT0cASaHp9pX+I0JWzM/LhiSg="; 
          };

          doCheck = false;
          
          # Add setuptools to build-system
          build-system = with pkgs.python3Packages; [ setuptools ];
          
          propagatedBuildInputs = with pkgs.python3Packages; [ requests beautifulsoup4 ];
        };

        pythonEnv = pkgs.python3.withPackages (ps: with ps; [
          mutagen
          musicbrainzngs
          thefuzz
          levenshtein 
          requests
          unidecode
          pillow
          beautifulsoup4
          lyricsgenius   # Our custom derivation
        ]);

      in
      {
        packages.default = pkgs.stdenv.mkDerivation {
          name = "swisstag";
          src = ./.;
          
          propagatedBuildInputs = [ pythonEnv ];
          
          installPhase = ''
            mkdir -p $out/bin
            cp swisstag.py $out/bin/swisstag
            chmod +x $out/bin/swisstag
            
            # Man Page Installation
            mkdir -p $out/share/man/man1
            cp swisstag.1 $out/share/man/man1/swisstag.1
          '';
          
          postFixup = ''
            sed -i '1s|^#!/usr/bin/env python3|#!${pythonEnv}/bin/python3|' $out/bin/swisstag
          '';

          meta = with pkgs.lib; {
            description = "Automated music tagger using Genius and MusicBrainz";
            homepage = "https://github.com/doromiert/swisstag";
            license = licenses.gpl3;
            platforms = platforms.all;
          };
        };

        apps.default = flake-utils.lib.mkApp {
          drv = self.packages.${system}.default;
        };

        devShells.default = pkgs.mkShell {
          buildInputs = [ pythonEnv ];
        };
      }
    );
}
