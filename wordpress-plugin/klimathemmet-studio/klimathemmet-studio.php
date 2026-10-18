<?php
/**
 * Plugin Name: Klimathemmet AI Studio
 * Description: Laddar artikeldesignen från AI Studio och gör SEO-fälten skrivbara via REST API för behöriga användare.
 * Version: 1.1.0
 * Requires at least: 6.0
 * Requires PHP: 7.4
 * Author: Klimathemmet
 * License: GPL-2.0-or-later
 */

if (!defined('ABSPATH')) {
    exit;
}

define('KH_STUDIO_VERSION', '1.1.0');

/**
 * Metafält som AI Studio får skriva via REST API. Bara användare som får redigera
 * inlägget kan läsa eller ändra dem (auth_callback).
 */
function kh_studio_meta_falt() {
    return array(
        '_kh_studio_id',          // markerar inlägg som skapats av AI Studio (skydd mot dubbelpublicering)
        '_yoast_wpseo_title',     // Yoast SEO
        '_yoast_wpseo_metadesc',
        '_yoast_wpseo_focuskw',   // fokusnyckelord (visas i SEO-kartan)
        'rank_math_title',        // Rank Math
        'rank_math_description',
        'rank_math_focus_keyword',
    );
}

add_action('init', function () {
    foreach (kh_studio_meta_falt() as $nyckel) {
        // Ett SEO-plugin som redan själv exponerar fältet får behålla sin egen registrering.
        if (registered_meta_key_exists('post', $nyckel, 'post')) {
            continue;
        }
        register_post_meta('post', $nyckel, array(
            'type'              => 'string',
            'single'            => true,
            'show_in_rest'      => true,
            'sanitize_callback' => 'sanitize_text_field',
            'auth_callback'     => function ($tillatet, $meta_key, $post_id) {
                return current_user_can('edit_post', $post_id);
            },
        ));
    }
});

/**
 * Artikeldesignen laddas som en vanlig stilmall, bara på inlägg som innehåller en AI Studio-artikel.
 * Då behöver ingen <style>-kod ligga i inläggen.
 */
add_action('wp_enqueue_scripts', function () {
    if (!is_singular()) {
        return;
    }
    $post = get_post();
    if ($post && strpos($post->post_content, 'kh-artikel') !== false) {
        wp_enqueue_style('kh-studio-artikel', plugins_url('artikel.css', __FILE__), array(), KH_STUDIO_VERSION);
    }
});

/** Låter AI Studio se att pluginet är installerat och vilken version det har. */
add_action('rest_api_init', function () {
    register_rest_route('klimathemmet-studio/v1', '/status', array(
        'methods'             => 'GET',
        'callback'            => function () {
            return array('version' => KH_STUDIO_VERSION);
        },
        'permission_callback' => function () {
            return current_user_can('edit_posts');
        },
    ));
});
