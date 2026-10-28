{
  description = "An experimental, partial port of DuckDB 2.0 to ESP32-P4";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in
    {
      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            # The pinned ESP-IDF installs its compiler and Python environment with make espidf-install.
            packages = with pkgs; [
              bashInteractive
              gcc
              gnumake
              git
              cmake
              ninja
              pkg-config
              (python315.withPackages (ps: [ ps.pyserial ]))
              uv
              ripgrep
              picocom
              clang-tools
              cppcheck
              valgrind
              pre-commit
              nixfmt
            ];
            shellHook = ''
              unset PYTHONPATH
              export IDF_PATH="$PWD/external/esp-idf"
              export IDF_TOOLS_PATH="''${IDF_TOOLS_PATH:-$PWD/build/espressif}"
            '';
          };
        }
      );
    };
}
