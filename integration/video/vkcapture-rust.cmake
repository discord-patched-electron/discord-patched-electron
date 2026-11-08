# MIT-licensed DPE producer control; linked into the GPL opted-in hooks.
# Bundled corresponding source can rebuild without the original checkout.
if(NOT DPE_GAME_ARCHIVE)
  find_program(DPE_CARGO cargo REQUIRED)
  set(DPE_RUST_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/dpe-rust")
  set(DPE_RUST_TARGET "${CMAKE_BINARY_DIR}/dpe-rust-target")
  set(DPE_GAME_ARCHIVE "${DPE_RUST_TARGET}/release/libdpe_game.a")
  file(GLOB_RECURSE DPE_RUST_INPUTS CONFIGURE_DEPENDS "${DPE_RUST_SOURCE}/native/*")
  add_custom_command(
    OUTPUT "${DPE_GAME_ARCHIVE}"
    COMMAND "${DPE_CARGO}" build --locked --release --package dpe-game
            --target-dir "${DPE_RUST_TARGET}"
    WORKING_DIRECTORY "${DPE_RUST_SOURCE}"
    DEPENDS ${DPE_RUST_INPUTS} "${DPE_RUST_SOURCE}/Cargo.toml"
            "${DPE_RUST_SOURCE}/Cargo.lock" "${DPE_RUST_SOURCE}/rust-toolchain.toml"
    VERBATIM)
  add_custom_target(dpe_game_rust DEPENDS "${DPE_GAME_ARCHIVE}")
endif()
foreach(DPE_HOOK VkLayer_obs_vkcapture obs_glcapture)
  if(TARGET dpe_game_rust)
    add_dependencies(${DPE_HOOK} dpe_game_rust)
  endif()
  target_link_libraries(${DPE_HOOK} "${DPE_GAME_ARCHIVE}" dl pthread m)
  target_compile_options(${DPE_HOOK} PRIVATE -ffunction-sections -fdata-sections)
  target_link_options(${DPE_HOOK} PRIVATE
    "-Wl,--gc-sections,--exclude-libs,ALL,--strip-debug,-z,relro,-z,now")
endforeach()
